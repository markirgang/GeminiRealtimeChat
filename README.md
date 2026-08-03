# Gemini Real-Time Video and Audio Chat

[![Deploy to Netlify](https://www.netlify.com/img/deploy/button.svg)](https://app.netlify.com/start/deploy?repository=https://github.com/markirgang/GeminiRealtimeChat)

A Python project for interacting with the Google Gemini Multimodal Live API using real-time video and audio.

## Setup

1. Create a virtual environment:
   ```bash
   python -m venv venv
   ```

2. Activate the virtual environment:
   - Windows: `venv\Scripts\activate`
   - Mac/Linux: `source venv/bin/activate`

3. Install requirements:
   ```bash
   pip install -r requirements.txt
   ```

4. Set your API key in the `.env` file.

5. Run the app:
   ```bash
   python main.py
   ```

## Project Structure

```
Birds/
├── esp32_led/
│   └── esp32_led.ino           # ESP32 dual-board firmware for GPIO controls
├── app.js                      # Web application frontend & Web Serial controller
├── index.html                  # Multimodal Web UI Dashboard
├── main.py                     # Backend server & Gemini Multimodal Live API session manager
├── style.css                   # Custom glassmorphic styling system
├── Birds On_Off Buttons ESP32.xlsx # GPIO pin mapping configuration
└── run.bat                     # Application launcher
```

## Git Commit Tree

To view the commit history graph in your terminal, run:
```bash
git tree
# Equivalent command:
git log --graph --oneline --all --decorate
```
