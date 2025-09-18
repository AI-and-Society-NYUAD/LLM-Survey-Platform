# LLM Survey Platform

This repository contains two main components:

1. **`survey.py`** — the Python backend that runs the LLM survey API.  
2. **`llmSurvey.html`** — the frontend interface served from your own web server.  

---

## 🚀 Backend: `survey.py`

This file needs to be hosted on a server that your **domain name** points to.

### Configuration
- **LLM agent key**: configure between **lines 15–18**.  
  - Example: we used **Groq** for our LLaMA agent ([https://groq.com](https://groq.com)).  
- **Domain name**: update **line 19** with your own domain.

### Running the Server
Start with Gunicorn using HTTPS:

```bash
gunicorn -w 4 \
  --certfile=/etc/letsencrypt/live/mydomain.com/fullchain.pem \
  --keyfile=/etc/letsencrypt/live/mydomain.com/privkey.pem \
  -b 0.0.0.0:5000 survey_v2:app
```

### Certificates
You will need a **trusted certificate** from Let’s Encrypt.

1. Install Certbot:
   ```bash
   sudo apt install certbot
   ```
2. Generate certs in standalone mode:
   ```bash
   sudo certbot certonly --standalone -d mydomain.com -d www.mydomain.com
   ```
3. Certbot will create:
   - `/etc/letsencrypt/live/mydomain.com/fullchain.pem`  
   - `/etc/letsencrypt/live/mydomain.com/privkey.pem`

4. Ensure your **DNS A record** points `mydomain.com` to your server’s IP.

---

## 🌐 Frontend: `llmSurvey.html`

This file must be hosted on a web server (e.g., Apache).  
Configure your DNS to map your domain name to the server’s IP.

### Sections to Configure

#### Consent Form
- Lines **163–181** contain the consent form.  
- `<br>` = line breaks, `<b>...</b>` = bold text.  
- Edit as needed for your study.

#### Initial Survey Questions
- Lines **231–325** define five initial survey questions.  
- Adjust wording/choices to your needs.

#### Chatbox LLM Agents
- Line **342**: `questionAIModel` maps questions to LLMs.  
  - Example: question 1 (index 0) uses **ChatGPT**.

#### Main Questions
- Line **350**: `categories` variable defines four categories.  
  - Each category has **two questions** (one is chosen randomly).  
  - Each question also specifies:
    - The LLM agent acting as the "expert"  
    - A **default answer** (taken from the LLM itself for consistency)  

- Lines **507–537**: system prompts for each LLM agent.

#### Final Message
- Line **599**: `finalMessage.innerHTML` contains the completion code.  
- This is used for **Prolific** participant verification.

#### Domain Name
- Replace **`XXXXXX.com`** with your actual domain name throughout the HTML file.  
- There are multiple instances that need updating.

---

## ⚡ Quick Start

1. **Clone the repository**  
   ```bash
   git clone https://github.com/your-username/llm-survey.git
   cd llm-survey
   ```

2. **Edit configuration**  
   - Open `survey.py`:
     - Lines 15–18 → add your LLM API keys.  
     - Line 19 → set your own domain.  
   - Open `llmSurvey.html`:
     - Update consent form, questions, LLM mappings, and replace `XXXXXX.com` with your domain.

3. **Install dependencies**  
   ```bash
   pip install fastapi uvicorn gunicorn
   sudo apt install certbot
   ```

4. **Get certificates**  
   ```bash
   sudo certbot certonly --standalone -d mydomain.com -d www.mydomain.com
   ```

5. **Run the backend**  
   ```bash
   gunicorn -w 4 \
     --certfile=/etc/letsencrypt/live/mydomain.com/fullchain.pem \
     --keyfile=/etc/letsencrypt/live/mydomain.com/privkey.pem \
     -b 0.0.0.0:5000 survey_v2:app
   ```

6. **Host the frontend**  
   - Place `llmSurvey.html` on your web server (Apache, Nginx, etc.).  
   - Make sure your DNS points `mydomain.com` → your server IP.

7. **Test your deployment**  
   - Visit: `https://mydomain.com/llmSurvey.html`  
   - Confirm:
     - Consent form is correct  
     - Survey questions load  
     - LLM chatbox connects  

---

## ✅ Summary

- **Backend (`survey.py`)** → runs with Gunicorn + HTTPS using Let’s Encrypt certificates.  
- **Frontend (`llmSurvey.html`)** → served from your web server (Apache or similar).  
- Update domain name, consent text, survey questions, LLM configs, and final message before deployment.  
- Once configured, participants can access your survey securely via HTTPS.


Citation
--------
    @misc{aldahoul_2025_LLM,
      title={Large Language Models are often politically extreme, usually ideologically inconsistent, and persuasive even in informational contexts}, 
      author={Nouar Aldahoul and Hazem Ibrahim and Matteo Varvello and Aaron Kaufman and Talal Rahwan and Yasir Zaki},
      year={2025},
      eprint={2505.04171},
      archivePrefix={arXiv},
      primaryClass={cs.CY},
      url={https://arxiv.org/abs/2505.04171}, 
   }
