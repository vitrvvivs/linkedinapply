#!/usr/bin/env python3
import requests, argparse, atexit, json, re
import webbrowser
from urllib.parse import unquote
from html2text import html2text
from binascii import b2a_base64
from getpass import getpass
from lxml import html
from sys import exit
from os import path

# reference globals
ROOT_URL =          "https://www.linkedin.com"
LOGIN_URL =         ROOT_URL + "/uas/login"
LOGIN_URL_POST =    ROOT_URL + "/uas/login-submit"
LOGIN_TOKENS =      ('loginCsrfParam', 'csrfToken', 'sourceAlias')
JOBS_COUNT =        50
JOBS_URL =          ROOT_URL + "/jobs/searchRefresh?keywords={}&location={}&start={{}}&count=" + str(JOBS_COUNT)
APPLY_URL_POST =    ROOT_URL + "/jobs/submitJobApplication"
RESUME_URL_POST =   ROOT_URL + "/mupld/cappts"
EXPERIENCE_LEVELS = ['not applicable', 'internship', 'entry', 'associate', 'mid-senior', 'director', 'executive']

# Mutable globals
headers =       {
                    'referer': "https://www.linkedin.com/",
                    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:48.0) Gecko/20100101 Firefox/48.0'
                }
login_payload = {
                    'loginCsrfParam': '',
                    'csrfToken': '',
                    'sourceAlias': '',
                    'session_key': '',
                    'session_password': ''
                }
apply_payload = {   # unchanged, but necessary
                    'spSrc': '',
                    'trkSrc': '',
                    'refId': '',
                    'store_securely': 'true',
                    'sign_response': 'true',
                    'persist': 'true',
                    # filled in based on job_data
                    'email': '',
                    'phone': '',
                    'csrfToken': '',
                    'jobID': '',
                    'upload_info': '',
                    'followCompany': 'false',
                    'resumeMediaId': '',#/md5sum.pdf
                    'resumeName': '',#resume.pdf
                    'resumeMupldSignature': ''#token
                }
apply_methods = {
                }
session = requests.session()

## convenience methods
def clip_string(source, start, end):
    return re.search('{}(.*){}'.format(start, end), source).group(1)
def get_job_description_module(job_url):
    # job description is in a json object inside a comment inside a code tag,
    # when getting the page (instead of calling the API)
    return json.loads(str(html.fromstring(session.get(job_url).text).xpath("//code[@id='decoratedJobPostingModule']/comment()")[0]).lstrip("<!--").rstrip("-->"))["decoratedJobPosting"]

## connect and login
def login(username='', password=''):
    #  get login page
    login_page = session.get(LOGIN_URL)
    login_page = html.fromstring(login_page.text)
    for token in LOGIN_TOKENS:
        value = login_page.xpath("//input[@name='{}']/@value".format(token))
        login_payload[token] = value[0]
    #  get email and password if not in argv
    if not username: username = input('Email: ')
    if not password: password = getpass('Password: ')
    login_attempt = session.post(
            LOGIN_URL_POST,
            data=dict(login_payload, **{
                    'session_key': username,
                    'session_password': password
                }),
            headers=dict(headers, **{
                    'referer': LOGIN_URL,
                    'X-IsAJAXForm': '1',
                })
        )
    if login_attempt.status_code != 200 or login_attempt.json()['status'] == 'fail':
        print("Failed to log in.")
        exit(1)

## Build a list of jobs
# returns a generator
def joblist(keywords, location, experience=[], record_file=None, blacklist=[]):
    jobs_url = JOBS_URL.format(keywords, location)
    jobs = []
    if experience:
        jobs_url += '&f_E=' + '%2C'.join([str(EXPERIENCE_LEVELS.index(x)) for x in experience])

    start = 0
    lim = JOBS_COUNT + 1
    while start + JOBS_COUNT < lim:
        jobs_json = session.get(jobs_url.format(start))
        jobs_json = json.loads(jobs_json.text)['decoratedJobPostingsModule']
        lim = jobs_json['paging']['total']
        for job in jobs_json['elements']:
            inapply = job['isInApply']
            url = job['viewJobTextUrl']
            job = job['decoratedJobPosting']
            job = {
                    'id': job['jobPosting']['id'],
                    'url': url,
                    'method': 'InApply' if inapply else job['jobPosting'].get('sourceDomain'),
                    'title': job['jobPosting']['title'],
                    'company': job['companyName'],
                    'description': job['formattedDescription']
                }
            # 'job' doesn't look like a real word anymore
            yield job
        start += JOBS_COUNT

## Apply using LinkedIn's builtin
#  returns a Requests response object
def InApply(job, resume_file): # `job` has 'id', resume_file is file object or False
    job_data = session.get("https://www.linkedin.com/jobs/view/applyFlow/{}".format(job['id'])).json() #json of application information
    payload = apply_payload
    # upload resume
    if resume_file:
        resume_file.seek(0) # each application sent reads the file; if we send it more than once, we need to reset the file to the beginning
        resume = session.post(
                RESUME_URL_POST,
                data={
                        'upload_info': job_data['applicant']['resumeUploadLink'],
                        'store_securely': 'true',
                        'sign_response': 'true',
                        'persist': 'true'
                    },
                headers=dict(headers, **{
                        'X-IsAJAXForm': '1',
                        'X-Requested-With': 'XMLHttpRequest',
                    }),
                files={'file': (
                        'resume.pdf',
                        resume_file,
                        'application/pdf',
                        {'Expires': '0'}
                    )}
            )
        # TODO allow for not-pdf resumes
        # get return data from resume upload; it's a json object inside a javascript call
        resume_json = html.fromstring(resume.text).xpath('//script/text()')[0]
        resume_json = json.loads(clip_string(resume_json, 'parent.mediaCallback\(', '\)'))
        payload = dict(payload, **{
                'resumeMediaId': resume_json['value'],
                'resumeName': resume_json['filename'],
                'resumeMupldSignature': resume_json['sig']
            })

    # send application
    application = session.post(
            APPLY_URL_POST,
            data=dict(payload, **{
                    'csrfToken': session.cookies['JSESSIONID'].replace('"', ''),
                    'jobId': job['id'],
                    'email': job_data['applicant']['email'][0]['email'],
                    'phone': job_data['applicant']['phone'],
                    'upload_info': job_data['applicant']['resumeUploadLink'],
                    'upload_info_with_js': job_data['applicant']['resumeUploadLink'],
                }),
            headers=dict(headers, **{
                    'X-IsAJAXForm': '1',
                    'X-Requested-With': 'XMLHttpRequest',
                })
        )
    # record job id, so that it's skipped next time
    return application
def InOffsiteOpen(job, resume_file):
    offsite_url = get_job_description_module(job['url'])["externalApplyLink"]
    offsite_url = clip_string(offsite_url, "url=", "&")
    offsite_url = unquote(offsite_url)

    print("opening", offsite_url, "in browser.\n")
    webbrowser.open(offsite_url)

apply_methods['InApply'] = InApply
apply_methods['offsite'] = InOffsiteOpen
# TODO add more apply_methods



def main(resume=None, username='', password='', keywords='', location='', blacklist='', experience='', yes_to_all=False, store_no=False, count=False):
    record_file = open(path.dirname(path.realpath(__file__))+'/applied.txt', 'a+')
    atexit.register(record_file.close)
    if resume:
        resume_file = open(resume, 'rb')
        atexit.register(resume_file.close)
    else:
        resume_file = False

    login(username, password)
    jobs = joblist(
            keywords,
            location,
            experience=experience,
            record_file=record_file,
            blacklist=blacklist
        )

    record_file.seek(0)
    formerly_applied = {int(x): True for x in record_file.read().split('\n') if x}
    if count: # returns number of jobs per provider
        from collections import Counter
        print(Counter([job['method'] for job in jobs]).most_common())
        return
    for job in jobs:
        method = apply_methods.get(job['method']) or apply_methods['offsite']

        if formerly_applied.get(job['id']) or job['company'] in blacklist:
            continue
        for k in ('title', 'company', 'method', 'description'):
            print(k, ':', job[k])

        while True:
            choice = input('apply? ') or 'yes'
            if choice[0] == 'm':
                description = get_job_description_module(job['url'])['jobPosting']['description']['rawText']
                print(description)
            elif (yes_to_all and job['method'] in apply_methods) or (choice[0] == 'y'):
                application = method(job, resume_file)
                record_file.write(str(job['id'])+'\n')
                break
            elif choice[0] == 'n' and store_no:
                record_file.write(str(job['id'])+'\n')
                break

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Mass apply to job postings on LinkedIn")

    ## handle command line flags
    parser.add_argument(
            '--username',
            help='LinkedIn username (email address)'
        )
    parser.add_argument(
            '--password',
        )
    parser.add_argument(
            '--keywords',
            help='Keywords to search',
            required=True,
        )
    parser.add_argument(
            '--location',
            help='City to search',
            required=True,
        )
    parser.add_argument(
            '--resume',
            help='location of resume file',
        )
    parser.add_argument(
            '--blacklist',
            help='comma-seperated string of blacklisted companies',
            default=''
        )
    parser.add_argument(
            '--experience',
            help='experience level to search',
            choices=EXPERIENCE_LEVELS,
            action='append'
        )
    parser.add_argument(
            '--yes-to-all',
            help='Dont\'t ask for confirmation before appyling',
            action='store_true'
        )
    parser.add_argument(
            '--store-no',
            help='store jobid of refused jobs',
            action='store_true'
        )
    parser.add_argument(
            '--count',
            help='Print number of jobs',
            action='store_true'
        )

    args = parser.parse_args()
    args.blacklist=[x.strip() for x in args.blacklist.split(',')]
    main(**vars(args))
