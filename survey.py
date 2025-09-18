from flask import Flask, request, jsonify
import openai  # Replace this with the GPT4o API library
from flask_cors import CORS  # Import Flask-CORS
from anthropic import Anthropic, HUMAN_PROMPT, AI_PROMPT  # For Claude
from groq import Groq
import os
from mistralai import Mistral
import time

app = Flask(__name__)

CORS(app)  # Enable CORS for all routes

# Replace with your GPT4o API key and configuration
GPT4O_API_KEY = "XXXXXXXXX"
anthropic_client = Anthropic(api_key="XXXXXXXXX")
Groq_API_KEY = "XXXXXX"
MISTRAL_API_KEY = "XXXXXXX"
DOMAIN_NAME = "XXXXX.com"

def format_claude_prompt(history):
	"""
	Formats the conversation history for Claude by alternating between HUMAN_PROMPT and AI_PROMPT,
	and ensures the prompt ends with "\n\nAssistant:".
	"""
	prompt = ""
	for msg in history:
		if msg['role'] == 'user':
			prompt += f"{HUMAN_PROMPT} {msg['content']}"
		elif msg['role'] == 'assistant':
			prompt += f"{AI_PROMPT} {msg['content']}"
		elif msg['role'] == 'system':
			system = msg['content']

	# Ensure the prompt ends with "\n\nAssistant:"
	if not prompt.endswith(AI_PROMPT):
		prompt += AI_PROMPT

	return system, prompt


@app.route('/chat', methods=['POST'])
def chat():
	data = request.json
	history = data.get('history', [])
	model = data.get('model', 'ChatGPT')  # Default to ChatGPT
	question = data.get('question')
	prolificPID = data.get('prolificPID')

	print (history)

	if not history:
		return jsonify({"response": "No message provided."}), 400

	with open(f'results/{prolificPID}_chat', 'a') as file:
		file.write(f"Chat,{question},{time.time()}\n")
		file.write(f"Question: {history[-1]['content']}\n")

	#################################################################
	#####														#####
	#####					CHATGPT								#####
	#####														#####
	#################################################################

	if model == "ChatGPT":
		print ("------- CHATGPT -------")

		try:
			# Updated syntax for chat completion
			response = openai.ChatCompletion.create(
				model="gpt-4",  # Replace with the model you are using
				#model="gpt-3.5-turbo",
				api_key=GPT4O_API_KEY,
			messages=history
			)

			# Extract the AI's response
			ai_message = response['choices'][0]['message']['content']
			with open(f'results/{prolificPID}_chat', 'a') as file:
				file.write(f"Answer: {ai_message}\n\n")
			return jsonify({"response": ai_message})
		except Exception as e:
			print(f"Error: {e}")
			return jsonify({"response": "An error occurred while processing your request."}), 500

	#################################################################
	#####														#####
	#####					LLAMA								#####
	#####														#####
	#################################################################

	elif model == "Llama":
		print ("------- LLAMA -------")

		try:
			client = Groq(api_key=Groq_API_KEY)
			completion = client.chat.completions.create(
					model="llama-3.2-1b-preview",
					messages=history,
					# temperature=0,
					max_tokens=1024,
					# top_p=0.1,
			)

			# Extract the AI's response
			ai_message = completion.choices[0].message.content

			with open(f'results/{prolificPID}_chat', 'a') as file:
				file.write(f"Answer: {ai_message}\n\n")
			return jsonify({"response": ai_message})
		except Exception as e:
			print(f"Error: {e}")
			return jsonify({"response": "An error occurred while processing your request."}), 500

	#################################################################
	#####														#####
	#####					MISTRAL								#####
	#####														#####
	#################################################################

	elif model == "Mistral":
		print ("------- MISTRAL -------")

		try:
			model = "open-mistral-nemo"

			client = Mistral(api_key=MISTRAL_API_KEY)

			chat_response = client.chat.complete(
				model = model,
				messages = history,
			)

			ai_message = chat_response.choices[0].message.content

			with open(f'results/{prolificPID}_chat', 'a') as file:
				file.write(f"Answer: {ai_message}\n\n")
			return jsonify({"response": ai_message})
		except Exception as e:
			print(f"Error: {e}")
			return jsonify({"response": "An error occurred while processing your request."}), 500

	#################################################################
	#####														#####
	#####					CLAUDE								#####
	#####														#####
	#################################################################

	elif model == "Claude":
		try:
			system, Prompt = format_claude_prompt(history)
			print (system)
			print ("---------") 
			print (Prompt)

			message = anthropic_client.messages.create(
					model="claude-3-haiku-20240307",
					max_tokens=1024,
					system=system,
					temperature=0,
					messages=[
						{"role": "user", "content": Prompt}
				 ]
				)
			ai_message = message.content[0].text
			with open(f'results/{prolificPID}_chat', 'a') as file:
								file.write(f"Answer: {ai_message}\n\n")

			return jsonify({"response": ai_message})
		except Exception as e:
			print(f"Error: {e}")
			return jsonify({"response": "An error occurred while processing your request."}), 500

@app.route('/start', methods=['POST'])
def start():
	data = request.json
	prolificPID = data.get('prolificPID')
	question = data.get('question')
	showChatbox = data.get('showChatbox')

	with open(f'results/{prolificPID}_timing', 'a') as file:
		file.write(f"--START--,{question},{showChatbox},{time.time()}\n")

	return jsonify({"status": "success", "message": "Timing recorded"})

@app.route('/end', methods=['POST'])
def end():
	data = request.json
	prolificPID = data.get('prolificPID')
	question = data.get('question')
	showChatbox = data.get('showChatbox')

	with open(f'results/{prolificPID}_timing', 'a') as file:
		file.write(f"--END--,{question},{showChatbox},{time.time()}\n")

	return jsonify({"status": "success", "message": "Timing recorded"})

@app.route('/vote', methods=['POST'])
def vote():
	data = request.json
	
	user_vote = data.get('vote')
	prolificPID = data.get('prolificPID')
	question = data.get('question')
	category = data.get('category')
	showChatbox = data.get('showChatbox')

	# Log or store the data for analysis
	print(f"Vote: {user_vote}, Prolific PID: {prolificPID}, Question: {question}, category: {category}, showChatbox: {showChatbox}")

	# Optionally save to a database
	# Example: Save to a file
	with open(f'results/{prolificPID}', 'a') as file:
		file.write(f"Vote: {user_vote}, Question: {question}, category: {category}, showChatbox: {showChatbox}\n")

	if showChatbox:
		with open(f'results/{prolificPID}_chat', 'a') as file:
			file.write(f"---------------------\n")

	return jsonify({"status": "success", "message": "Vote recorded"})
	

# Survey endpoint
@app.route('/survey', methods=['POST'])
def handle_survey():
	try:
		# Parse JSON data from the request
		data = request.json
		Q1 = data.get('question1', [])
		Q2 = data.get('question2', [])
		Q3 = data.get('question3', '')
		Q4 = data.get('question4', '')
		Q5 = data.get('question5', '')
		prolificPID = data.get('prolificPID', '')

		# Log the data (for demonstration)
		print("Survey Response Received:")
		print(f"Question1: {Q1}")
		print(f"Question2: {Q2}")
		print(f"Question3: {Q3}")
		print(f"Question4: {Q4}")
		print(f"Question5: {Q5}")
		print(f"prolificPID: {prolificPID}")

		# Optionally store the data in a file
		with open(f'results/{prolificPID}', 'a') as f:
			f.write(f"Question 1: {Q1}\n")
			f.write(f"Question 2: {Q2}\n")
			f.write(f"Question 3: {Q3}\n")
			f.write(f"Question 4: {Q4}\n")
			f.write(f"Question 5: {Q5}\n")
			# f.write("\n")

		# Respond with success
		return jsonify({"status": "success", "message": "Survey response recorded"}), 200

	except Exception as e:
		# Handle errors
		print(f"Error: {e}")
		return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
	app.run(host='0.0.0.0', port=5000, debug=True, ssl_context=('/etc/letsencrypt/live/'+DOMAIN_NAME+'/fullchain.pem', '/etc/letsencrypt/live/'+DOMAIN_NAME+'/privkey.pem'))
