import os
import re
import json
import smtplib
import httpx
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from pathlib import Path
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from flask import Flask, render_template, send_from_directory, request, jsonify
from openai import OpenAI

# Force load environment variables using absolute path
script_dir = Path(__file__).parent.absolute()
env_path = script_dir / '.env'
load_dotenv(dotenv_path=env_path, override=True)

app = Flask(__name__)

# --- Logging Configuration ---
log_file = script_dir / 'team_sudarshan_debug.log'
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(str(log_file)),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("TeamSudarshan")
logger.info(f"--- Application Start: {datetime.now()} ---")

# --- PythonAnywhere Proxy Logic ---
def get_openai_client():
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        logger.error("CRITICAL: OPENAI_API_KEY is not set in environment variables.")
        return None

    is_pa = "pythonanywhere" in os.environ.get("PYTHONHOME", "").lower() or os.path.exists("/home/TeamSudarshan")
    
    if is_pa:
        proxy_url = "http://proxy.server:3128"
        logger.info(f"Detected PythonAnywhere environment. Using proxy: {proxy_url}")
        proxies = {
            "http://": proxy_url,
            "https://": proxy_url,
        }
        try:
            return OpenAI(
                api_key=api_key,
                http_client=httpx.Client(proxies=proxies)
            )
        except Exception as e:
            logger.error(f"Failed to initialize OpenAI client with proxy: {e}")
            return None
    else:
        logger.info("Running in local environment. Standard OpenAI client initialized.")
        return OpenAI(api_key=api_key)

client = get_openai_client()

# Configuration
TEAM_EMAIL = "qicteamsudarshan@gmail.com"
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
EMAIL_SENDER_PASSWORD = os.getenv("EMAIL_PASSWORD") 

def save_inquiry_locally(subject, body):
    """Saves inquiry to inquiries.json if email fails (common on PA Free Tier)."""
    leads_file = script_dir / 'inquiries.json'
    inquiry = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "subject": subject,
        "content": body
    }
    
    data = []
    if leads_file.exists():
        try:
            with open(leads_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except:
            data = []
            
    data.append(inquiry)
    
    with open(leads_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4)
    logger.info(f"Inquiry saved locally to inquiries.json (Backup due to SMTP restriction)")

def send_notification_email(subject, body):
    global EMAIL_SENDER_PASSWORD
    if not EMAIL_SENDER_PASSWORD:
        EMAIL_SENDER_PASSWORD = os.getenv("EMAIL_PASSWORD")
        
    if not EMAIL_SENDER_PASSWORD:
        logger.warning("Email password not found. Saving to local logs.")
        save_inquiry_locally(subject, body)
        return False

    try:
        msg = MIMEMultipart()
        msg['From'] = TEAM_EMAIL
        msg['To'] = TEAM_EMAIL
        msg['Subject'] = f"[Team Sudarshan Portal] {subject}"
        msg.attach(MIMEText(body, 'plain'))
        
        # This will likely fail on PA Free Tier with Errno 101
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=10)
        server.starttls()
        server.login(TEAM_EMAIL, EMAIL_SENDER_PASSWORD)
        server.send_message(msg)
        server.quit()
        logger.info(f"Notification email sent: {subject}")
        return True
    except Exception as e:
        logger.error(f"SMTP Error: {e}. Falling back to local inquiry log.")
        save_inquiry_locally(subject, body)
        return False

def extract_text_from_html(filename):
    research_dir = Path('templates/company_research')
    file_path = research_dir / filename
    if not file_path.exists():
        return ""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            soup = BeautifulSoup(f.read(), 'html.parser')
            for script in soup(["script", "style"]):
                script.decompose()
            return soup.get_text(separator=' ', strip=True)
    except Exception as e:
        logger.error(f"Error extracting text from {filename}: {e}")
        return ""

def get_company_files():
    research_dir = Path('templates/company_research')
    if not research_dir.exists():
        research_dir.mkdir(parents=True, exist_ok=True)
        return [], 0
    latest_companies = {}
    for filename in os.listdir(research_dir):
        if filename.endswith('.html'):
            file_path = research_dir / filename
            try:
                content = file_path.read_text(encoding='utf-8')
                company_name = filename.replace('.html', '').replace('_OLD', '')
                date_match = re.search(r'Date: (January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}', content, re.IGNORECASE)
                publish_date = datetime.min
                if date_match:
                    date_str = date_match.group(0).replace('Date: ', '')
                    publish_date = datetime.strptime(date_str, '%B %d, %Y')
                latest_companies[company_name] = {
                    'name': company_name,
                    'filename': filename,
                    'display_name': company_name.replace('_', ' ').replace('-', ' '),
                    'publish_date': publish_date
                }
            except: continue
    companies_by_date = {}
    for company in latest_companies.values():
        date_key = company['publish_date'].strftime('%B %d, %Y') if company['publish_date'] != datetime.min else "Undated"
        if date_key not in companies_by_date: companies_by_date[date_key] = []
        companies_by_date[date_key].append(company)
    sorted_dates = sorted(companies_by_date.items(), key=lambda item: datetime.strptime(item[0], '%B %d, %Y') if item[0] != "Undated" else datetime.min, reverse=True)
    return sorted_dates, len(latest_companies)

tools = [{
    "type": "function",
    "function": {
        "name": "record_user_interest",
        "description": "Call this ONLY after the user has provided their email address to connect with the team.",
        "parameters": {
            "type": "object",
            "properties": {
                "user_email": {"type": "string", "description": "The user's email address."},
                "user_name": {"type": "string", "description": "The user's name."},
                "details": {"type": "string", "description": "The question or context of the interest."}
            },
            "required": ["details", "user_email"]
        }
    }
}]

@app.route('/')
def index():
    companies_by_date, total_companies = get_company_files()
    return render_template('index.html', companies_by_date=companies_by_date, total_companies=total_companies)

@app.route('/company/<filename>')
def view_company(filename):
    report_title = filename.replace('.html', '').replace('_', ' ').replace('-', ' ')
    return render_template('view_report.html', filename=filename, report_title=report_title)

@app.route('/serve_report/<filename>')
def serve_report(filename):
    directory = os.path.join(app.root_path, 'templates', 'company_research')
    return send_from_directory(directory, filename)

@app.route('/api/chat', methods=['POST'])
def chat():
    global client
    if not client:
        client = get_openai_client()
        
    if not client:
        return jsonify({"error": "OpenAI client not initialized."}), 500

    data = request.json
    user_message = data.get('message', '')
    history = data.get('history', [])
    current_company = data.get('current_company', None)
    
    company_context = extract_text_from_html(current_company) if current_company else ""

    system_prompt = f"""You are the 'Team Sudarshan Investment Analyst'. 
Represent the team professionally for the Quantum Investment Championship.

### OPERATIONAL PROTOCOL ###

1. IF NO COMPANY IS SELECTED:
   - Inform the user you specialize in the research reports on this dashboard.
   - Instruct them to click on a company to begin analysis.
   - For general inquiries, ask for their name and email, then use 'record_user_interest'.

2. IF A COMPANY IS SELECTED:
   - Answer based ONLY on the provided data.
   - Context: {company_context}

3. HANDLING UNKNOWN QUESTIONS:
   - Politely inform them you'll escalate to senior analysts.
   - Ask for Name and Email Address.
   - DO NOT call 'record_user_interest' until you have an email address.

Concise and institutional tone only."""

    messages = [{"role": "system", "content": system_prompt}] + history + [{"role": "user", "content": user_message}]

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            tools=tools,
            tool_choice="auto"
        )
        response_message = response.choices[0].message
        
        if response_message.tool_calls:
            for tool_call in response_message.tool_calls:
                args = json.loads(tool_call.function.arguments)
                send_notification_email(f"Inquiry from {args.get('user_name', 'User')}", f"Email: {args.get('user_email')}\nDetails: {args.get('details')}")
                messages.append(response_message)
                messages.append({"role": "tool", "tool_call_id": tool_call.id, "name": "record_user_interest", "content": "Success recorded in system."})
            
            final_response = client.chat.completions.create(model="gpt-4o-mini", messages=messages)
            return jsonify({"response": final_response.choices[0].message.content})
        
        return jsonify({"response": response_message.content})
    except Exception as e:
        logger.error(f"Chat API Error: {str(e)}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)