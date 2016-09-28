#!/usr/bin/env python3
import requests, argparse, atexit, json, re
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
session = requests.session()

## connect and login
def login():
    #  get login page
    login_page = session.get(LOGIN_URL)
    login_page = html.fromstring(login_page.text)
    for token in LOGIN_TOKENS:
        value = login_page.xpath("//input[@name='{}']/@value".format(token))
        login_payload[token] = value[0]
    #  get email and password if not in argv
    if not login_payload['session_key']:
        login_payload['session_key'] = input('Email: ')
    if not login_payload['session_password']:
        login_payload['session_key'] = getpass('Password: ')
    login_attempt = session.post(
            LOGIN_URL_POST,
            data=login_payload,
            headers=dict(headers, **{
                    'referer': LOGIN_URL,
                    'X-IsAJAXForm': '1',
                })
        )
    if login_attempt.status_code != 200 or login_attempt.json()['status'] == 'fail':
        print("Failed to log in.")
        exit(1)

## Build a list of jobs
#  ignore jobs already applied to
#  ignore companies on blacklist
def buildjoblist(keywords, location, record_file=None, blacklist=[]):
    jobs_url = JOBS_URL.format(keywords, location)
    jobs = []
    if record_file:
        record_file.seek(0)
        formerly_applied = {int(x): True for x in record_file.read().split('\n') if x}

    start = 0
    count = JOBS_COUNT
    lim = count + 1
    while start + count < lim:
        jobs_json = session.get(jobs_url.format(start))
        jobs_json = json.loads(jobs_json.text)['decoratedJobPostingsModule']
        lim = jobs_json['paging']['total']
        for job in jobs_json['elements']:
            inapply = job['isInApply']
            job = job['decoratedJobPosting']
            job = {
                    'id': job['jobPosting']['id'],
                    'method': 'InApply' if inapply else job['jobPosting'].get('sourceDomain'),
                    'title': job['jobPosting']['title'],
                    'company': job['companyName'],
                    'description': job['formattedDescription']
                }
            # 'job' doesn't look like a real word anymore
            if (record_file and formerly_applied.get(job['id'])) or job['company'] in blacklist:
                continue
            jobs.append(job)
        start += count
    return jobs

## Apply using LinkedIn's builtin
#  returns a Requests response object
def InApply(job, resume_file): # `job` has 'id', resume_file is file object or False
    job_data = session.get("https://www.linkedin.com/jobs/view/applyFlow/{}".format(job['id'])).json() #json of application information
    payload = apply_payload
    # upload resume
    if resume_file:
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
        resume_json = json.loads(re.search('{}(.*){}'.format('parent.mediaCallback\(', '\)'), resume_json).group(1))
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

# TODO add more sourceDomain handlers
apply_methods = {
        'InApply': InApply
    }

def main(resume=None, username='', password='', keywords='', location='', blacklist='', yes_to_all=False, store_no=False):
    if username: login_payload['session_key'] = username
    if password: login_payload['session_password'] = password
    record_file = open(path.dirname(path.realpath(__file__))+'/applied.txt', 'a+')
    atexit.register(record_file.close)
    if resume:
        resume_file = open(resume, 'rb')
        atexit.register(resume_file.close)
    else:
        resume_file = False

    login()
    jobs = buildjoblist(
            keywords,
            location,
            record_file=record_file,
            blacklist=blacklist
        )
    for job in jobs:
        method = apply_methods.get(job['method'])
        if not method: continue
        for k in ('title', 'company', 'description'):
            print(k, ':', job[k])
        if yes_to_all or ((input('apply? ') or 'yes')[0] != 'n'):
            application = method(job, resume_file)
            print(application.status_code)
            record_file.write(str(job['id'])+'\n')
            return
        elif store_no:
            record_file.write(str(job['id'])+'\n')

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
            '--yes-to-all',
            help='Dont\'t ask for confirmation before appyling',
            action='store_true'
        )
    parser.add_argument(
            '--store-no',
            help='store jobid of refused jobs',
            action='store_true'
        )
    args = parser.parse_args()
    args.blacklist=[x.strip() for x in args.blacklist.split(',')]

    main(**vars(args))
