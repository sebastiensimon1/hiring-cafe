from flask import Flask, request, jsonify
import requests
from flask_cors import CORS
import os
import time
from html import unescape
import re
from functools import wraps
import random
from datetime import datetime, timedelta

app = Flask(__name__)
CORS(app)

# Cache for storing search results
cache = {}
CACHE_DURATION = timedelta(hours=1)  # Cache results for 1 hour

# Rate limiting with backoff
last_request_time = 0
MIN_REQUEST_INTERVAL = 5  # Increased from 3 to 5 seconds
request_count = 0
MAX_REQUESTS_PER_HOUR = 50  # Limit requests per hour
request_timestamps = []

# User agent rotation for anti-bot
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0"
]

def get_cache_key(job_title, workplace_types, location_filter, page, size):
    """Generate cache key for search results"""
    workplace_str = ','.join(sorted(workplace_types))
    return f"{job_title}:{workplace_str}:{location_filter}:{page}:{size}"

def check_hourly_limit():
    """Check if hourly request limit is exceeded"""
    global request_timestamps
    now = datetime.now()
    one_hour_ago = now - timedelta(hours=1)
    
    # Remove old timestamps
    request_timestamps = [ts for ts in request_timestamps if ts > one_hour_ago]
    
    if len(request_timestamps) >= MAX_REQUESTS_PER_HOUR:
        return False
    return True

def rate_limit(f):
    """Decorator to enforce rate limiting with exponential backoff"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        global last_request_time, request_count, request_timestamps
        
        # Check hourly limit
        if not check_hourly_limit():
            return None, f"Hourly request limit ({MAX_REQUESTS_PER_HOUR}) exceeded. Please try again later."
        
        current_time = time.time()
        time_since_last = current_time - last_request_time
        
        # Calculate delay with jitter to avoid pattern detection
        base_delay = MIN_REQUEST_INTERVAL
        jitter = random.uniform(0.5, 2.0)
        required_delay = base_delay + jitter
        
        if time_since_last < required_delay:
            sleep_time = required_delay - time_since_last
            print(f"⏳ Rate limiting: waiting {sleep_time:.1f} seconds...")
            time.sleep(sleep_time)
        
        last_request_time = time.time()
        request_count += 1
        request_timestamps.append(datetime.now())
        
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
def search_jobs_api(job_title, workplace_types=None, page=0, size=40, max_retries=3):
    """Search for jobs on hiring.cafe with retry logic"""
    if workplace_types is None:
        workplace_types = ["Remote", "Hybrid", "On-site"]
    
    url = "https://hiring.cafe/api/search-jobs"
    
    payload = {
        "size": min(size, 100),  # Cap at 100
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
    
    for attempt in range(max_retries):
        # Rotate user agent
        user_agent = random.choice(USER_AGENTS)
        
        headers = {
            "Content-Type": "application/json",
            "Accept": "*/*",
            "Origin": "https://hiring.cafe",
            "Referer": f"https://hiring.cafe/?searchState=%7B%22searchQuery%22%3A%22{job_title.replace(' ', '+')}%22%7D",
            "User-Agent": user_agent,
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
            "Accept-Language": "en-US,en;q=0.9"
        }
        
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=(10, 30))
            response.raise_for_status()
            return response.json(), None
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 403:
                if attempt < max_retries - 1:
                    wait_time = (2 ** attempt) * 10  # Exponential backoff: 10s, 20s, 40s
                    print(f"⚠️ Rate limited. Waiting {wait_time}s before retry {attempt + 2}/{max_retries}")
                    time.sleep(wait_time)
                    continue
                return None, "Rate limited by hiring.cafe. Service temporarily unavailable."
            return None, f"HTTP Error: {e.response.status_code}"
            
        except requests.exceptions.Timeout:
            if attempt < max_retries - 1:
                print(f"⏳ Timeout. Retrying {attempt + 2}/{max_retries}...")
                time.sleep(3)
                continue
            return None, "Request timeout. Please try again."
            
        except Exception as e:
            return None, str(e)
    
    return None, "Max retries exceeded"

@rate_limit
def get_job_details_api(job_id, max_retries=3):
    """Get detailed job information by ID with retry logic"""
    build_id = "T5BbkPhTrZW7uSyfwsbxs"
    url = f"https://hiring.cafe/_next/data/{build_id}/viewjob/{job_id}.json"
    
    for attempt in range(max_retries):
        user_agent = random.choice(USER_AGENTS)
        
        headers = {
            "Accept": "*/*",
            "X-Nextjs-Data": "1",
            "User-Agent": user_agent,
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
            "Referer": f"https://hiring.cafe/viewjob/{job_id}"
        }
        
        try:
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            return response.json(), None
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 403 and attempt < max_retries - 1:
                wait_time = (2 ** attempt) * 10
                print(f"⚠️ Rate limited. Waiting {wait_time}s before retry {attempt + 2}/{max_retries}")
                time.sleep(wait_time)
                continue
            return None, f"HTTP Error: {e.response.status_code}"
            
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(2)
                continue
            return None, str(e)
    
    return None, "Max retries exceeded"

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
        'version': '2.0.0',
        'features': [
            'Search caching (1 hour)',
            'Rate limiting with exponential backoff',
            'User agent rotation',
            f'Max {MAX_REQUESTS_PER_HOUR} requests per hour'
        ],
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
            },
            '/stats': {
                'method': 'GET',
                'description': 'Get API usage statistics'
            }
        },
        'rate_limits': {
            'min_interval': f'{MIN_REQUEST_INTERVAL}s between requests',
            'max_hourly': f'{MAX_REQUESTS_PER_HOUR} requests per hour',
            'cache_duration': '1 hour'
        }
    })

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint for Railway"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'requests_made': request_count,
        'cache_size': len(cache)
    }), 200

@app.route('/stats', methods=['GET'])
def stats():
    """Get API usage statistics"""
    one_hour_ago = datetime.now() - timedelta(hours=1)
    recent_requests = len([ts for ts in request_timestamps if ts > one_hour_ago])
    
    return jsonify({
        'total_requests': request_count,
        'requests_last_hour': recent_requests,
        'limit_per_hour': MAX_REQUESTS_PER_HOUR,
        'remaining_this_hour': max(0, MAX_REQUESTS_PER_HOUR - recent_requests),
        'cache_entries': len(cache),
        'cache_duration_hours': CACHE_DURATION.total_seconds() / 3600
    })

@app.route('/search-jobs', methods=['POST'])
def search_jobs():
    """
    Search for jobs with caching
    
    Request body:
    {
        "job_title": "software engineer",
        "workplace_types": ["Remote", "Hybrid", "On-site"],  // optional
        "location_filter": "United States",  // optional
        "page": 0,  // optional, default 0
        "size": 40  // optional, default 40, max 100
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
        size = min(data.get('size', 40), 100)  # Cap at 100
        
        # Check cache first
        cache_key = get_cache_key(job_title, workplace_types, location_filter, page, size)
        
        if cache_key in cache:
            cache_entry = cache[cache_key]
            if datetime.now() - cache_entry['timestamp'] < CACHE_DURATION:
                print(f"✅ Returning cached results for: {job_title}")
                return jsonify({
                    'success': True,
                    'cached': True,
                    'cached_at': cache_entry['timestamp'].isoformat(),
                    **cache_entry['data']
                })
            else:
                # Cache expired, remove it
                del cache[cache_key]
        
        print(f"\n=== JOB SEARCH START ===")
        print(f"Title: {job_title}")
        print(f"Workplace: {workplace_types}")
        print(f"Location filter: {location_filter}")
        print(f"Size: {size}")
        
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
        
        response_data = {
            'total': total,
            'filtered': len(formatted_jobs),
            'page': page,
            'jobs': formatted_jobs
        }
        
        # Cache the results
        cache[cache_key] = {
            'timestamp': datetime.now(),
            'data': response_data
        }
        
        # Clean old cache entries (keep max 100)
        if len(cache) > 100:
            oldest_key = min(cache.keys(), key=lambda k: cache[k]['timestamp'])
            del cache[oldest_key]
        
        return jsonify({
            'success': True,
            'cached': False,
            **response_data
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
        # Check cache
        cache_key = f"job:{job_id}"
        if cache_key in cache:
            cache_entry = cache[cache_key]
            if datetime.now() - cache_entry['timestamp'] < CACHE_DURATION:
                print(f"✅ Returning cached job details for: {job_id}")
                return jsonify({
                    'success': True,
                    'cached': True,
                    'job': cache_entry['data']
                })
        
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
        formatted_job = format_job_data(job)
        
        # Add full description for detailed view
        description = job_info.get('description', '')
        formatted_job['description'] = clean_html(description) if description else None
        formatted_job['description_html'] = description
        
        # Cache the result
        cache[cache_key] = {
            'timestamp': datetime.now(),
            'data': formatted_job
        }
        
        print(f"✅ Job details retrieved: {formatted_job['title']}")
        
        return jsonify({
            'success': True,
            'cached': False,
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
    print(f"⏱️  Rate limit: {MIN_REQUEST_INTERVAL}s between requests")
    print(f"📊 Max requests per hour: {MAX_REQUESTS_PER_HOUR}")
    print(f"💾 Cache duration: {CACHE_DURATION.total_seconds() / 3600} hours")
    app.run(host='0.0.0.0', port=port, debug=False)
