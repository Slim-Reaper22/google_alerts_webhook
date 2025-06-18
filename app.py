# app.py - Flask webhook to receive Google Alerts from Zapier
# This script can fetch article content and use AI summaries

from flask import Flask, request, jsonify
import os
import re
from datetime import datetime
from bs4 import BeautifulSoup
import requests
from urllib.parse import unquote
import json

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
            print(f"Processing alert {i+1}/{len(alerts)}: {alert['headline'][:50]}...")
            
            # Fetch article content if URL exists
            if alert['url']:
                article_data = fetch_article_content(alert['url'])
                
                if article_data['content']:
                    # Extract information from article
                    full_text = f"{alert['headline']} {article_data['content']}"
                    
                    alert['company'] = extract_company_name(full_text)
                    alert['address'] = extract_location(full_text)
                    alert['estimated_jobs'] = extract_job_numbers(full_text)
                    
                    # Generate AI summary if available
                    if anthropic_client:
                        alert['lead_summary'] = generate_ai_summary(
                            article_data['content'], 
                            alert['headline']
                        )
                    else:
                        # Create manual summary
                        alert['lead_summary'] = create_manual_summary(
                            alert['headline'],
                            alert['company'],
                            alert['address'],
                            article_data['content']
                        )
                else:
                    alert['lead_summary'] = alert['headline']
            
            alert['date'] = email_date
            
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
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

def parse_google_alert_email(html_content, subject):
    """Parse Google Alert email HTML"""
    alerts = []
    
    try:
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # Find all links that look like article links
        all_links = soup.find_all('a', href=True)
        
        for link in all_links:
            href = link.get('href', '')
            
            # Skip Google's own links
            if any(skip in href for skip in ['google.com/alerts', 'mailto:', '#']):
                continue
            
            # Extract actual URL from Google redirect
            actual_url = extract_google_url(href)
            
            if actual_url:
                alert = {
                    'headline': link.get_text(strip=True),
                    'url': actual_url,
                    'source': '',
                    'company': '',
                    'address': '',
                    'lead_summary': '',
                    'estimated_jobs': ''
                }
                
                # Try to find source (usually in green text near the link)
                next_element = link.find_next_sibling() or link.parent.find_next_sibling()
                if next_element:
                    # Look for green colored text
                    source_elem = next_element.find('font', color='#006621') or \
                                 next_element.find(style=lambda x: x and 'color' in x and ('006621' in x or 'green' in x))
                    if source_elem:
                        alert['source'] = source_elem.get_text(strip=True)
                
                # Only add if we have a headline
                if alert['headline'] and len(alert['headline']) > 10:
                    alerts.append(alert)
        
    except Exception as e:
        print(f"Error parsing email: {e}")
    
    return alerts[:10]  # Limit to 10 alerts to avoid timeout

def extract_google_url(url):
    """Extract actual URL from Google's redirect URL"""
    if 'google.com/url?' in url:
        match = re.search(r'url=([^&]+)', url)
        if match:
            return unquote(match.group(1))
    elif url.startswith('http'):
        return url
    return None

def fetch_article_content(url):
    """Fetch and parse article content"""
    article_data = {
        'content': '',
        'title': '',
        'success': False
    }
    
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Extract title
        title_elem = soup.find('title')
        if title_elem:
            article_data['title'] = title_elem.get_text(strip=True)
        
        # Remove script and style elements
        for elem in soup(['script', 'style']):
            elem.decompose()
        
        # Try to find main content
        content_selectors = [
            'article',
            '[class*="content"]',
            '[class*="article"]',
            'main',
            '[role="main"]'
        ]
        
        content = None
        for selector in content_selectors:
            elem = soup.select_one(selector)
            if elem:
                paragraphs = elem.find_all('p')
                if len(paragraphs) >= 2:
                    content = ' '.join([p.get_text(strip=True) for p in paragraphs])
                    break
        
        # Fallback: get all paragraphs
        if not content:
            all_p = soup.find_all('p')
            if all_p:
                content = ' '.join([p.get_text(strip=True) for p in all_p[:15]])
        
        article_data['content'] = content[:3000] if content else ''  # Limit length
        article_data['success'] = bool(content)
        
    except Exception as e:
        print(f"Error fetching article from {url}: {e}")
    
    return article_data

def extract_company_name(text):
    """Extract company name using various patterns"""
    patterns = [
        # Company with suffix
        r'([A-Z][A-Za-z0-9\s&\-\.\']+(?:Inc|LLC|Corp|Corporation|Company|Co|Ltd|Limited|Group|Holdings|Industries|Manufacturing|Logistics|Properties|Partners|Enterprises|Systems|Technologies|Solutions)\.?)',
        # Company doing action
        r'([A-Z][A-Za-z0-9\s&\-\.\']+)\s+(?:announced|announces|plans|planning|expands|expanding|opens|opening|launches|launching|develops|developing|acquires|acquiring|invests|investing)',
        # Quoted company
        r'["\']([A-Z][A-Za-z0-9\s&\-\.\']+)["\']',
    ]
    
    companies = []
    for pattern in patterns:
        matches = re.findall(pattern, text[:1000])  # Check first 1000 chars
        companies.extend(matches)
    
    # Clean and filter
    companies = [c.strip() for c in companies if len(c.strip()) > 3 and len(c.strip()) < 50]
    
    # Return most likely company (first one found)
    return companies[0] if companies else ""

def extract_location(text):
    """Extract city and state location"""
    # US States mapping
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
    
    # Build pattern with all states
    all_states = list(states.keys()) + list(states.values())
    state_pattern = '|'.join(all_states)
    
    # Location patterns
    patterns = [
        rf'(?:in|at|to|near)\s+([A-Z][a-zA-Z\s]+?),\s*({state_pattern})',
        rf'([A-Z][a-zA-Z\s]+?),\s*({state_pattern})',
        rf'([A-Z][a-zA-Z\s]+?)\s+({state_pattern})\s+(?:facility|plant|warehouse|center)',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            city = match.group(1).strip()
            state = match.group(2).strip()
            # Normalize state to full name
            if state.upper() in states:
                state = states[state.upper()]
            return f"{city}, {state}"
    
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

def generate_ai_summary(content, headline):
    """Generate AI summary using Claude"""
    if not anthropic_client:
        return create_manual_summary(headline, "", "", content)
    
    try:
        prompt = f"""Based on this article about light industrial expansion, provide a brief 2-3 sentence summary focusing on:
1. What company is expanding or opening
2. What type of facility/operation it is
3. Key details like location, size, timeline, or investment

Article headline: {headline}
Article content: {content[:2000]}

Summary:"""

        response = anthropic_client.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=150,
            temperature=0.3,
            messages=[{"role": "user", "content": prompt}]
        )
        
        return response.content[0].text.strip()
        
    except Exception as e:
        print(f"AI summary error: {e}")
        return create_manual_summary(headline, "", "", content)

def create_manual_summary(headline, company, location, content):
    """Create a summary without AI"""
    summary_parts = []
    
    if company:
        summary_parts.append(f"{company}")
    
    if location:
        summary_parts.append(f"in {location}")
    
    # Extract facility type from content
    facility_keywords = ['warehouse', 'distribution center', 'manufacturing', 'facility', 'plant', 'logistics']
    for keyword in facility_keywords:
        if keyword in content.lower():
            summary_parts.append(f"{keyword}")
            break
    
    if summary_parts:
        return f"{' '.join(summary_parts[:3])}. {headline}"
    else:
        return headline[:200]

def send_to_smartsuite(alert_data):
    """Send record to SmartSuite"""
    try:
        url = f"https://app.smartsuite.com/api/v1/applications/{SMARTSUITE_TABLE_ID}/records/"
        
        headers = {
            "Authorization": f"Token {SMARTSUITE_API_KEY}",
            "ACCOUNT-ID": SMARTSUITE_WORKSPACE,
            "Content-Type": "application/json"
        }
        
        # Format date
        try:
            if 'date' in alert_data and alert_data['date']:
                date_str = alert_data['date']
                # Try to parse and reformat
                formatted_date = {"date": date_str, "include_time": True}
            else:
                formatted_date = {"date": datetime.now().isoformat(), "include_time": True}
        except:
            formatted_date = {"date": datetime.now().isoformat(), "include_time": True}
        
        # Build payload with CORRECT FIELD IDs
        payload = {
            "title": (alert_data.get('company') or alert_data.get('headline', 'New Lead'))[:100],
            "sc373e6626": alert_data.get('company', ''),  # company
            "s46434c9b6": {  # address (addressfield type might need special format)
                "location": alert_data.get('address', ''),
                "street_address": alert_data.get('address', '')
            },
            "s492934214": alert_data.get('lead_summary', '')[:500],  # lead_summary
            "sa8ca8dbcb": alert_data.get('estimated_jobs', ''),  # estimated_new_jobs
            "s8e6e9fe79": {  # article_url (linkfield)
                "url": alert_data.get('url', ''),
                "label": "Read Article"
            },
            "s8d5616e3e": {"from_date": formatted_date},  # date
            "s6e74e1ce5": alert_data.get('source', '')[:100]  # source
        }
        
        # Clean payload - remove empty values
        payload = {k: v for k, v in payload.items() if v}
        
        print(f"Sending to SmartSuite: {payload.get('title', 'Unknown')}")
        
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
