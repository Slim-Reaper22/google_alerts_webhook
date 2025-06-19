# app.py - Flask webhook to receive Google Alerts from Zapier
# Enhanced version with better article fetching and AI extraction

from flask import Flask, request, jsonify
import os
import re
from datetime import datetime
from bs4 import BeautifulSoup
import requests
from urllib.parse import unquote
import json
import time

# Optional: Anthropic for AI summaries
try:
    from anthropic import Anthropic
    anthropic_client = Anthropic(api_key=os.environ.get('ANTHROPIC_API_KEY')) if os.environ.get('ANTHROPIC_API_KEY') else None
except:
    anthropic_client = None

app = Flask(__name__)

# Configuration - Now using environment variables
SMARTSUITE_API_KEY = os.environ.get('SMARTSUITE_API_KEY')
SMARTSUITE_WORKSPACE = os.environ.get('SMARTSUITE_WORKSPACE', 'sxs77u60')
SMARTSUITE_TABLE_ID = os.environ.get('SMARTSUITE_TABLE_ID', '68517b0036a5ddf3941ea848')

@app.route('/')
def home():
    return """
    <h1>Google Alerts to SmartSuite Webhook</h1>
    <p>Status: Running ✅</p>
    <p>Endpoint: POST /webhook</p>
    """

@app.route('/webhook', methods=['POST'])
def process_google_alert():
    """Receive Google Alert from Zapier and process it"""
    try:
        # Get data from Zapier
        data = request.json
        print(f"Received webhook data: {data.keys()}")
        
        # Extract email data
        email_body = data.get('body_html', '') or data.get('body_plain', '')
        email_subject = data.get('subject', '')
        email_date = data.get('date', datetime.now().isoformat())
        
        if not email_body:
            return jsonify({
                'status': 'error',
                'message': 'No email body provided'
            }), 400
        
        # Parse Google Alert email
        alerts = parse_google_alert_email(email_body, email_subject)
        print(f"Found {len(alerts)} alerts in email")
        
        # Process each alert
        results = []
        for i, alert in enumerate(alerts):
            print(f"\n{'='*60}")
            print(f"Processing alert {i+1}/{len(alerts)}: {alert['headline'][:50]}...")
            print(f"URL: {alert['url']}")
            
            # Fetch article content if URL exists
            if alert['url']:
                print(f"Fetching article content from: {alert['url']}")
                article_data = fetch_article_content_enhanced(alert['url'])
                
                print(f"Article fetch success: {article_data['success']}")
                print(f"Content length: {len(article_data['content'])}")
                
                if article_data['content'] and anthropic_client:
                    # Use AI to extract ALL information at once
                    print("Using AI to extract information...")
                    extracted_info = extract_all_info_with_ai(
                        article_data['content'], 
                        alert['headline']
                    )
                    
                    alert['company'] = extracted_info['company']
                    alert['address'] = extracted_info['address']
                    alert['estimated_jobs'] = extracted_info['jobs']
                    alert['lead_summary'] = extracted_info['summary']
                    
                    print(f"AI Extracted:")
                    print(f"  Company: {alert['company']}")
                    print(f"  Address: {alert['address']}")
                    print(f"  Jobs: {alert['estimated_jobs']}")
                else:
                    # Fallback to pattern matching
                    print("No article content or AI - using pattern matching")
                    alert['company'] = extract_company_name(alert['headline'])
                    alert['address'] = extract_location_from_headline(alert['headline'])
                    alert['estimated_jobs'] = extract_job_numbers(alert['headline'])
                    alert['lead_summary'] = create_detailed_summary(
                        alert['headline'],
                        alert['company'],
                        alert['address'],
                        ""
                    )
            else:
                print("No URL provided")
                alert['lead_summary'] = alert['headline']
            
            alert['date'] = email_date
            
            print(f"Final alert data:")
            print(f"  Company: {alert['company']}")
            print(f"  Address: {alert['address']}")
            print(f"  Jobs: {alert['estimated_jobs']}")
            print(f"  Summary: {alert['lead_summary'][:100]}...")
            
            # Send to SmartSuite
            success, message = send_to_smartsuite(alert)
            
            results.append({
                'headline': alert['headline'],
                'company': alert['company'],
                'success': success,
                'message': message
            })
        
        # Return results
        return jsonify({
            'status': 'success',
            'processed': len(results),
            'sent_to_smartsuite': sum(1 for r in results if r['success']),
            'results': results
        })
        
    except Exception as e:
        print(f"Error processing webhook: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

def parse_google_alert_email(html_content, subject):
    """Parse Google Alert email HTML - improved version"""
    alerts = []
    
    try:
        soup = BeautifulSoup(html_content, 'html.parser')
        
        print("Parsing Google Alert email...")
        
        # Find all table rows that contain alerts
        for table in soup.find_all('table'):
            for row in table.find_all('tr'):
                # Look for links in the row
                link = row.find('a', href=True)
                if not link:
                    continue
                
                href = link.get('href', '')
                
                # Skip Google's own links
                if any(skip in href for skip in ['google.com/alerts', 'mailto:', '#', 'support.google']):
                    continue
                
                # Extract actual URL from Google redirect
                actual_url = extract_google_url(href)
                
                if actual_url:
                    # Get the full text content from the link
                    headline_parts = []
                    for text in link.stripped_strings:
                        headline_parts.append(text)
                    
                    # Join with spaces and clean up
                    headline_text = ' '.join(headline_parts)
                    
                    # Add spaces between camelCase and fix spacing
                    headline_text = fix_text_spacing(headline_text)
                    
                    alert = {
                        'headline': headline_text.strip(),
                        'url': actual_url,
                        'source': '',
                        'company': '',
                        'address': '',
                        'lead_summary': '',
                        'estimated_jobs': ''
                    }
                    
                    # Try to find source
                    for elem in row.find_all(['font', 'span']):
                        if elem.get('color') == '#006621' or (elem.get('style') and '006621' in elem.get('style', '')):
                            alert['source'] = elem.get_text(strip=True)
                            break
                    
                    # Only add if we have a meaningful headline
                    if alert['headline'] and len(alert['headline']) > 10:
                        alerts.append(alert)
                        print(f"Found alert: {alert['headline'][:50]}...")
        
        # If no alerts found in tables, try direct link search
        if not alerts:
            print("No alerts in tables, trying direct link search...")
            all_links = soup.find_all('a', href=True)
            
            for link in all_links:
                href = link.get('href', '')
                
                if any(skip in href for skip in ['google.com/alerts', 'mailto:', '#']):
                    continue
                
                actual_url = extract_google_url(href)
                
                if actual_url:
                    headline_parts = []
                    for text in link.stripped_strings:
                        headline_parts.append(text)
                    
                    headline_text = ' '.join(headline_parts)
                    headline_text = fix_text_spacing(headline_text)
                    
                    alert = {
                        'headline': headline_text.strip(),
                        'url': actual_url,
                        'source': '',
                        'company': '',
                        'address': '',
                        'lead_summary': '',
                        'estimated_jobs': ''
                    }
                    
                    if alert['headline'] and len(alert['headline']) > 10:
                        alerts.append(alert)
        
    except Exception as e:
        print(f"Error parsing email: {e}")
        import traceback
        traceback.print_exc()
    
    print(f"Total alerts found: {len(alerts)}")
    return alerts[:10]  # Limit to 10 alerts

def fix_text_spacing(text):
    """Fix spacing issues in text"""
    # Add space between lowercase and uppercase
    text = re.sub(r'([a-z])([A-Z])', r'\1 \2', text)
    # Add space between letter and uppercase letter
    text = re.sub(r'([a-zA-Z])([A-Z][a-z])', r'\1 \2', text)
    # Fix common patterns
    text = re.sub(r'Company([A-Z])', r'Company \1', text)
    text = re.sub(r'Expands([A-Z])', r'Expands \1', text)
    text = re.sub(r'Announces([A-Z])', r'Announces \1', text)
    text = re.sub(r'Million([A-Z])', r'Million \1', text)
    text = re.sub(r'Manufacturing([A-Z])', r'Manufacturing \1', text)
    # Normalize spaces
    text = re.sub(r'\s+', ' ', text)
    return text

def extract_google_url(url):
    """Extract actual URL from Google's redirect URL"""
    if 'google.com/url?' in url:
        match = re.search(r'url=([^&]+)', url)
        if match:
            return unquote(match.group(1))
    elif url.startswith('http'):
        return url
    return None

def fetch_article_content_enhanced(url):
    """Enhanced article fetching with multiple strategies"""
    article_data = {
        'content': '',
        'title': '',
        'success': False
    }
    
    try:
        # Try different user agents
        user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        ]
        
        for user_agent in user_agents:
            headers = {
                'User-Agent': user_agent,
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1'
            }
            
            response = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
            
            if response.status_code == 200:
                soup = BeautifulSoup(response.content, 'html.parser')
                
                # Extract title
                title_elem = soup.find('title')
                if title_elem:
                    article_data['title'] = title_elem.get_text(strip=True)
                
                # Remove script and style elements
                for elem in soup(['script', 'style', 'noscript']):
                    elem.decompose()
                
                # Try multiple content extraction strategies
                content = ""
                
                # Strategy 1: Look for article tags
                article_elem = soup.find('article')
                if article_elem:
                    paragraphs = article_elem.find_all(['p', 'div'])
                    content = ' '.join([p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True)])
                
                # Strategy 2: Look for main content divs
                if not content:
                    for selector in ['[class*="content"]', '[class*="article"]', '[id*="content"]', '[id*="article"]', 'main']:
                        elem = soup.select_one(selector)
                        if elem:
                            paragraphs = elem.find_all(['p', 'div'])
                            content = ' '.join([p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True)])
                            if content:
                                break
                
                # Strategy 3: Get all paragraphs
                if not content:
                    all_p = soup.find_all('p')
                    if len(all_p) > 3:
                        content = ' '.join([p.get_text(strip=True) for p in all_p if len(p.get_text(strip=True)) > 50])
                
                # Strategy 4: Get all text
                if not content:
                    content = soup.get_text()
                    # Clean up the text
                    content = re.sub(r'\n+', ' ', content)
                    content = re.sub(r'\s+', ' ', content)
                
                if content and len(content) > 100:
                    article_data['content'] = content[:5000]  # Limit length
                    article_data['success'] = True
                    break
            
            elif response.status_code == 403:
                print(f"Access forbidden (403) for {url}")
                # Try with a delay
                time.sleep(2)
            
    except Exception as e:
        print(f"Error fetching article from {url}: {e}")
    
    return article_data

def extract_all_info_with_ai(content, headline):
    """Use AI to extract all information at once"""
    if not anthropic_client:
        return {
            'company': extract_company_name(headline),
            'address': extract_location_from_headline(headline),
            'jobs': extract_job_numbers(headline),
            'summary': create_detailed_summary(headline, "", "", content)
        }
    
    try:
        prompt = f"""Analyze this article about industrial expansion and extract the following information:

1. COMPANY NAME: Extract the exact company name (just the company, no description)
2. ADDRESS/LOCATION: Extract the complete address if available, or at minimum the city and state. If a full street address is mentioned, include it.
3. ESTIMATED NEW JOBS: Extract the number of new jobs if mentioned (just the number)
4. SUMMARY: Write a comprehensive paragraph summary about the article that focuses more on the facilty. ex. sq footage, purpose of the facility, etc...

Article headline: {headline}
Article content: {content[:3000]}

Respond in this exact JSON format:
{{
    "company": "Company Name",
    "address": "Full address or City, State",
    "jobs": "Number or empty string",
    "summary": "Full paragraph summary"
}}"""

        response = anthropic_client.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=300,
            temperature=0.1,
            messages=[{"role": "user", "content": prompt}]
        )
        
        # Parse the JSON response
        response_text = response.content[0].text.strip()
        
        # Extract JSON from response (in case there's extra text)
        json_match = re.search(r'\{[^}]+\}', response_text, re.DOTALL)
        if json_match:
            response_text = json_match.group(0)
        
        extracted = json.loads(response_text)
        
        return {
            'company': extracted.get('company', ''),
            'address': extracted.get('address', ''),
            'jobs': extracted.get('jobs', ''),
            'summary': extracted.get('summary', headline)
        }
        
    except Exception as e:
        print(f"AI extraction error: {e}")
        # Fallback to pattern matching
        return {
            'company': extract_company_name(headline),
            'address': extract_location_from_headline(headline),
            'jobs': extract_job_numbers(headline),
            'summary': create_detailed_summary(headline, "", "", content)
        }

def extract_company_name(text):
    """Extract company name using various patterns"""
    # Fix spacing first
    text = fix_text_spacing(text)
    
    patterns = [
        # Company with suffix
        r'([A-Z][A-Za-z0-9\s&\-\.\']+?)\s*(?:Inc\.?|LLC|Corp\.?|Corporation|Company|Co\.?|Ltd\.?|Limited|Group|Holdings|Industries|Manufacturing|Logistics|Properties|Partners|Enterprises|Systems|Technologies|Solutions)\b',
        # Company before action verb
        r'^([A-Z][A-Za-z0-9\s&\-\.\']+?)\s+(?:Announces|Expands|Opens|Plans|Invests|Develops|Acquires|to Build|Will Build)',
        # Company in quotes
        r'["\']([A-Z][A-Za-z0-9\s&\-\.\']+?)["\']',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            company = match.group(1).strip()
            # Clean up
            company = re.sub(r'\s+', ' ', company)
            if 3 < len(company) < 50:
                return company
    
    return ""

def extract_location_from_headline(text):
    """Extract ONLY location from headline - no company names"""
    # Fix spacing first
    text = fix_text_spacing(text)
    
    # Remove company names and common words first
    text = re.sub(r'([A-Z][A-Za-z0-9\s&\-\.\']+?)\s*(?:Inc\.?|LLC|Corp\.?|Corporation|Company|Co\.?|Ltd\.?)', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\b(?:Announces|Expands|Opens|Plans|Million|Manufacturing|Expansion|Operations|Facility)\b', '', text, flags=re.IGNORECASE)
    
    # US States
    states = {
        'AL': 'Alabama', 'AK': 'Alaska', 'AZ': 'Arizona', 'AR': 'Arkansas',
        'CA': 'California', 'CO': 'Colorado', 'CT': 'Connecticut', 'DE': 'Delaware',
        'FL': 'Florida', 'GA': 'Georgia', 'HI': 'Hawaii', 'ID': 'Idaho',
        'IL': 'Illinois', 'IN': 'Indiana', 'IA': 'Iowa', 'KS': 'Kansas',
        'KY': 'Kentucky', 'LA': 'Louisiana', 'ME': 'Maine', 'MD': 'Maryland',
        'MA': 'Massachusetts', 'MI': 'Michigan', 'MN': 'Minnesota', 'MS': 'Mississippi',
        'MO': 'Missouri', 'MT': 'Montana', 'NE': 'Nebraska', 'NV': 'Nevada',
        'NH': 'New Hampshire', 'NJ': 'New Jersey', 'NM': 'New Mexico', 'NY': 'New York',
        'NC': 'North Carolina', 'ND': 'North Dakota', 'OH': 'Ohio', 'OK': 'Oklahoma',
        'OR': 'Oregon', 'PA': 'Pennsylvania', 'RI': 'Rhode Island', 'SC': 'South Carolina',
        'SD': 'South Dakota', 'TN': 'Tennessee', 'TX': 'Texas', 'UT': 'Utah',
        'VT': 'Vermont', 'VA': 'Virginia', 'WA': 'Washington', 'WV': 'West Virginia',
        'WI': 'Wisconsin', 'WY': 'Wyoming'
    }
    
    # Look for state and work backwards
    for state_abbr, state_full in states.items():
        # Try both abbreviation and full name
        for state_form in [state_abbr, state_full]:
            pattern = rf'([A-Z][a-zA-Z\s]+?),?\s*{re.escape(state_form)}\b'
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                city = match.group(1).strip()
                # Clean city name
                city = re.sub(r'\b\d+\b', '', city)  # Remove numbers
                city = re.sub(r'\s+', ' ', city).strip()
                if city and len(city) > 2:
                    return f"{city}, {state_full}"
    
    return ""

def extract_job_numbers(text):
    """Extract job creation numbers"""
    patterns = [
        r'(\d{1,3}(?:,\d{3})*)\s*(?:new\s+)?(?:jobs?|positions?|employees?|workers?)',
        r'(?:create|creating|add|adding|hire|hiring)\s+(?:up\s+to\s+)?(\d{1,3}(?:,\d{3})*)',
        r'(?:employ|employing)\s+(?:up\s+to\s+)?(\d{1,3}(?:,\d{3})*)',
        r'workforce\s+of\s+(\d{1,3}(?:,\d{3})*)',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1)
    
    return ""

def create_detailed_summary(headline, company, location, content):
    """Create a detailed paragraph summary"""
    # Fix spacing in headline first
    headline = fix_text_spacing(headline)
    
    # Start building the summary
    summary = ""
    
    # Company part
    if company:
        summary = f"{company} "
    else:
        # Try to extract from headline
        company_match = re.search(r'^([A-Z][A-Za-z0-9\s&\-\.\']+?)\s+(?:Announces|Expands|Opens)', headline, re.IGNORECASE)
        if company_match:
            summary = f"{company_match.group(1).strip()} "
        else:
            summary = "A company "
    
    # Action part
    if "expands" in headline.lower():
        summary += "is expanding its operations "
    elif "announces" in headline.lower() and "expansion" in headline.lower():
        summary += "has announced plans for a major expansion "
    elif "opens" in headline.lower():
        summary += "is opening a new facility "
    elif "invests" in headline.lower():
        summary += "is making a significant investment "
    elif "develops" in headline.lower():
        summary += "is developing new facilities "
    else:
        summary += "has announced new industrial development "
    
    # Facility type
    if "warehouse" in headline.lower():
        summary += "with a new warehouse facility "
    elif "distribution" in headline.lower():
        summary += "with a distribution center "
    elif "manufacturing" in headline.lower():
        summary += "with manufacturing operations "
    elif "logistics" in headline.lower():
        summary += "with logistics facilities "
    else:
        summary += ""
    
    # Location
    if location:
        summary += f"in {location}. "
    else:
        # Try to extract from headline
        loc = extract_location_from_headline(headline)
        if loc:
            summary += f"in {loc}. "
        else:
            summary += "at a new location. "
    
    # Investment amount
    investment_match = re.search(r'\$(\d+(?:,\d+)*(?:\.\d+)?)\s*(million|billion)?', headline, re.IGNORECASE)
    if investment_match:
        amount = investment_match.group(0)
        summary += f"The project represents an investment of {amount}. "
    
    # Jobs
    job_match = re.search(r'(\d+(?:,\d+)*)\s*(?:new\s+)?(?:jobs?|positions?)', headline, re.IGNORECASE)
    if job_match:
        jobs = job_match.group(0)
        summary += f"The expansion is expected to create {jobs}. "
    
    # Add context if still short
    if len(summary) < 150:
        if "manufacturing" in headline.lower():
            summary += "This expansion strengthens the region's manufacturing sector and contributes to local economic growth. "
        elif "distribution" in headline.lower() or "warehouse" in headline.lower():
            summary += "The new facility will enhance distribution capabilities and support growing logistics demands in the region. "
        else:
            summary += "This development represents continued investment in light industrial infrastructure and local job creation. "
    
    # Ensure minimum length
    if len(summary) < 200:
        summary += "The project demonstrates the ongoing growth of industrial operations in the area. "
    
    return summary.strip()

def send_to_smartsuite(alert_data):
    """Send record to SmartSuite"""
    try:
        # Check if we have API key
        if not SMARTSUITE_API_KEY:
            print("ERROR: No SmartSuite API key found in environment variables!")
            return False, "Missing SmartSuite API key"
            
        url = f"https://app.smartsuite.com/api/v1/applications/{SMARTSUITE_TABLE_ID}/records/"
        
        headers = {
            "Authorization": f"Token {SMARTSUITE_API_KEY}",
            "ACCOUNT-ID": SMARTSUITE_WORKSPACE,
            "Content-Type": "application/json"
        }
        
        # Format date
        try:
            if 'date' in alert_data and alert_data['date']:
                from dateutil import parser
                date_obj = parser.parse(alert_data['date'])
                formatted_date = date_obj.isoformat()
            else:
                formatted_date = datetime.now().isoformat()
        except:
            formatted_date = datetime.now().isoformat()
        
        # Create unique title
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_title = (alert_data.get('company') or alert_data.get('headline', 'New Lead'))[:80]
        unique_title = f"{base_title} - {timestamp}"
        
        # Build payload
        payload = {
            "title": unique_title,
            "sc373e6626": alert_data.get('company', ''),  # company
            "s46434c9b6": alert_data.get('address', ''),  # address - as text field
            "s492934214": alert_data.get('lead_summary', '')[:1000],  # lead_summary - longer
            "sa8ca8dbcb": alert_data.get('estimated_jobs', ''),  # estimated_new_jobs
            "s8e6e9fe79": alert_data.get('url', ''),  # article_url
            "s8d5616e3e": formatted_date,  # date
            "s6e74e1ce5": alert_data.get('source', '')[:100]  # source
        }
        
        # Clean payload - remove empty values
        payload = {k: v for k, v in payload.items() if v and v != ''}
        
        print(f"Sending to SmartSuite: {unique_title}")
        
        response = requests.post(url, headers=headers, json=payload)
        
        if response.status_code in [200, 201]:
            return True, "Successfully sent to SmartSuite"
        else:
            error_msg = f"SmartSuite error {response.status_code}: {response.text[:200]}"
            print(error_msg)
            return False, error_msg
            
    except Exception as e:
        error_msg = f"Exception: {str(e)}"
        print(error_msg)
        return False, error_msg

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
