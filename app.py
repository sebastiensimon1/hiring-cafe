from flask import Flask, request, jsonify
import requests
from flask_cors import CORS
import os
import time
from html import unescape
import re
from functools import wraps

app = Flask(__name__)
CORS(app)

# Rate limiting
last_request_time = 0
MIN_REQUEST_INTERVAL = 3  # seconds between requests

def rate_limit(f):
    """Decorator to enforce rate limiting"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        global last_request_time
        current_time = time.time()
        time_since_last = current_time - last_request_time
        
        if time_since_last < MIN_REQUEST_INTERVAL:
            sleep_time = MIN_REQUEST_INTERVAL - time_since_last
            print(f"⏳ Rate limiting: waiting {sleep_time:.1f} seconds...")
            time.sleep(sleep_time)
        
        last_request_time = time.time()
        return f(*args, **kwargs)
    return decorated_function

def clean_html(html_text):
    """Remove HTML tags and clean text"""
    if not html_text:
        return ""
    text = re.sub(r'<[^>]+>', ' ', html_text)
    text = unescape(text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

@rate_limit
def search_jobs_api(job_title, workplace_types=None, page=0, size=40):
    """Search for jobs on hiring.cafe"""
    if workplace_types is None:
        workplace_types = ["Remote", "Hybrid", "On-site"]
    
    url = "https://hiring.cafe/api/search-jobs"
    
    payload = {
        "size": size,
        "page": page,
        "searchState": {
            "workplaceTypes": workplace_types,
            "commitmentTypes": ["Full Time", "Part Time", "Contract", "Internship"],
            "seniorityLevel": ["No Prior Experience Required", "Entry Level", "Mid Level", "Senior Level"],
            "searchQuery": job_title,
            "dateFetchedPastNDays": 121,
            "sortBy": "default"
        }
    }
    
    headers = {
        "Content-Type": "application/json",
        "Accept": "*/*",
        "Origin": "https://hiring.cafe",
        "Referer": f"https://hiring.cafe/?searchState=%7B%22searchQuery%22%3A%22{job_title.replace(' ', '+')}%22%7D",
        "User-Agent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Mobile Safari/537.36",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty"
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=(10, 30))
        response.raise_for_status()
        return response.json(), None
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 403:
            return None, "Rate limited by hiring.cafe. Please try again in a few minutes."
        return None, f"HTTP Error: {e.response.status_code}"
    except Exception as e:
        return None, str(e)

@rate_limit
def get_job_details_api(job_id):
    """Get detailed job information by ID"""
    build_id = "T5BbkPhTrZW7uSyfwsbxs"
    url = f"https://hiring.cafe/_next/data/{build_id}/viewjob/{job_id}.json"
    
    headers = {
        "Accept": "*/*",
        "X-Nextjs-Data": "1",
        "User-Agent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
        "Referer": f"https://hiring.cafe/viewjob/{job_id}"
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        return response.json(), None
    except Exception as e:
        return None, str(e)

def filter_by_location(jobs, location_filter):
    """Filter jobs by location"""
    if not location_filter:
        return jobs
    
    location_lower = location_filter.lower()
    filtered_jobs = []
    
    for job in jobs:
        job_data = job.get('v5_processed_job_data', {})
        location = job_data.get('formatted_workplace_location', '').lower()
        countries = [c.lower() for c in job_data.get('workplace_countries', [])]
        states = [s.lower() for s in job_data.get('workplace_states', [])]
        cities = [c.lower() for c in job_data.get('workplace_cities', [])]
        
        if (location_lower in location or 
            any(location_lower in c for c in countries) or
            any(location_lower in s for s in states) or
            any(location_lower in c for c in cities)):
            filtered_jobs.append(job)
    
    return filtered_jobs

def format_job_data(job):
    """Format job data for API response"""
    job_data = job.get('v5_processed_job_data', {})
    company_data = job.get('v5_processed_company_data', {})
    job_info = job.get('job_information', {})
    
    return {
        'id': job.get('id'),
        'title': job_data.get('core_job_title') or job_info.get('title'),
        'company': company_data.get('name') or job_data.get('company_name'),
        'location': job_data.get('formatted_workplace_location'),
        'workplace_type': job_data.get('workplace_type'),
        'commitment': job_data.get('commitment', []),
        'seniority_level': job_data.get('seniority_level'),
        'role_type': job_data.get('role_type'),
        'salary': {
            'yearly_min': job_data.get('yearly_min_compensation'),
            'yearly_max': job_data.get('yearly_max_compensation'),
            'hourly_min': job_data.get('hourly_min_compensation'),
            'hourly_max': job_data.get('hourly_max_compensation'),
            'currency': job_data.get('listed_compensation_currency'),
            'frequency': job_data.get('listed_compensation_frequency')
        } if job_data.get('yearly_min_compensation') or job_data.get('hourly_min_compensation') else None,
        'experience_required': job_data.get('min_industry_and_role_yoe'),
        'education': {
            'bachelors': job_data.get('bachelors_degree_requirement'),
            'masters': job_data.get('masters_degree_requirement'),
            'fields': job_data.get('bachelors_degree_fields_of_study', [])
        },
        'certifications': job_data.get('licenses_or_certifications', []),
        'technical_tools': job_data.get('technical_tools', []),
        'requirements_summary': job_data.get('requirements_summary'),
        'apply_url': job.get('apply_url'),
        'view_url': f"https://hiring.cafe/viewjob/{job.get('id')}" if job.get('id') else None,
        'posted_date': job_data.get('estimated_publish_date'),
        'benefits': {
            'visa_sponsorship': job_data.get('visa_sponsorship', False),
            'relocation_assistance': job_data.get('relocation_assistance', False),
            'tuition_reimbursement': job_data.get('tuition_reimbursement', False),
            'retirement_plan': job_data.get('retirement_plan', False),
            'parental_leave': job_data.get('generous_parental_leave', False)
        }
    }

@app.route('/', methods=['GET'])
def home():
    """Health check and API documentation"""
    return jsonify({
        'status': 'online',
        'service': 'Hiring.cafe Job Search API',
        'version': '1.0.0',
        'endpoints': {
            '/search-jobs': {
                'method': 'POST',
                'description': 'Search for jobs by title',
                'example': {
                    'job_title': 'software engineer',
                    'workplace_types': ['Remote'],
                    'location_filter': 'United States',
                    'page': 0,
                    'size': 20
                }
            },
            '/job/<job_id>': {
                'method': 'GET',
                'description': 'Get detailed job information by ID',
                'example': '/job/lever___avertium___135159ab-3e8f-4d1d-b811-f8ad0638ea96'
            }
        },
        'rate_limit': f'{MIN_REQUEST_INTERVAL} seconds between requests',
        'note': 'This API is a proxy to hiring.cafe and respects their rate limits'
    })

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint for Railway"""
    return jsonify({'status': 'healthy'}), 200

@app.route('/search-jobs', methods=['POST'])
def search_jobs():
    """
    Search for jobs
    
    Request body:
    {
        "job_title": "software engineer",
        "workplace_types": ["Remote", "Hybrid", "On-site"],  // optional
        "location_filter": "United States",  // optional
        "page": 0,  // optional, default 0
        "size": 40  // optional, default 40
    }
    """
    try:
        data = request.json
        
        if not data or 'job_title' not in data:
            return jsonify({
                'error': 'Missing required parameter',
                'message': 'job_title is required',
                'example': {
                    'job_title': 'software engineer',
                    'workplace_types': ['Remote'],
                    'location_filter': 'United States'
                }
            }), 400
        
        job_title = data['job_title']
        workplace_types = data.get('workplace_types', ['Remote', 'Hybrid', 'On-site'])
        location_filter = data.get('location_filter')
        page = data.get('page', 0)
        size = data.get('size', 40)
        
        print(f"\n=== JOB SEARCH START ===")
        print(f"Title: {job_title}")
        print(f"Workplace: {workplace_types}")
        print(f"Location filter: {location_filter}")
        
        # Search jobs
        results, error = search_jobs_api(job_title, workplace_types, page, size)
        
        if error:
            return jsonify({
                'error': 'Search failed',
                'message': error
            }), 503
        
        if not results:
            return jsonify({
                'error': 'No results',
                'message': 'Search returned no data'
            }), 404
        
        jobs = results.get('results', [])
        total = results.get('nbHits', len(jobs))
        
        # Apply location filter if provided
        if location_filter:
            jobs = filter_by_location(jobs, location_filter)
        
        # Format jobs
        formatted_jobs = [format_job_data(job) for job in jobs]
        
        print(f"✅ Found {len(formatted_jobs)} jobs (filtered from {total} total)")
        
        return jsonify({
            'success': True,
            'total': total,
            'filtered': len(formatted_jobs),
            'page': page,
            'jobs': formatted_jobs
        })
        
    except Exception as e:
        print(f"❌ Error in search-jobs: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'error': 'Internal server error',
            'details': str(e)
        }), 500

@app.route('/job/<job_id>', methods=['GET'])
def get_job(job_id):
    """
    Get detailed job information by ID
    
    Example: /job/lever___avertium___135159ab-3e8f-4d1d-b811-f8ad0638ea96
    """
    try:
        print(f"\n=== JOB DETAILS REQUEST ===")
        print(f"Job ID: {job_id}")
        
        # Get job details
        results, error = get_job_details_api(job_id)
        
        if error:
            return jsonify({
                'error': 'Failed to fetch job details',
                'message': error
            }), 503
        
        # Extract job from Next.js response
        page_props = results.get('pageProps', {})
        job = page_props.get('job', {})
        
        if not job:
            return jsonify({
                'error': 'Job not found',
                'message': f'No job found with ID: {job_id}'
            }), 404
        
        # Format detailed job data
        job_info = job.get('job_information', {})
        job_data = job.get('v5_processed_job_data', {})
        company_data = job.get('v5_processed_company_data', {})
        
        formatted_job = format_job_data(job)
        
        # Add full description for detailed view
        description = job_info.get('description', '')
        formatted_job['description'] = clean_html(description) if description else None
        formatted_job['description_html'] = description
        
        print(f"✅ Job details retrieved: {formatted_job['title']}")
        
        return jsonify({
            'success': True,
            'job': formatted_job
        })
        
    except Exception as e:
        print(f"❌ Error in get-job: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'error': 'Internal server error',
            'details': str(e)
        }), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    print(f"🚀 Starting Hiring.cafe Job Search API on 0.0.0.0:{port}")
    print(f"⏱️  Rate limit: {MIN_REQUEST_INTERVAL} seconds between requests")
    app.run(host='0.0.0.0', port=port, debug=False)
