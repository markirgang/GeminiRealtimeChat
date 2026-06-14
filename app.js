// app.js

// Inline AudioWorklet processor code as a Blob to keep everything self-contained in a single file
const workletCode = `
class AudioProcessor extends AudioWorkletProcessor {
  process(inputs, outputs, parameters) {
    const input = inputs[0];
    if (input && input[0]) {
      const inputChannel = input[0]; // Float32Array
      
      // Convert Float32 samples (-1.0 to 1.0) to Int16 PCM (-32768 to 32767)
      const int16Buffer = new Int16Array(inputChannel.length);
      for (let i = 0; i < inputChannel.length; i++) {
        let s = Math.max(-1, Math.min(1, inputChannel[i]));
        int16Buffer[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
      }
      
      // Post the converted PCM chunk back to the main thread
      this.port.postMessage(int16Buffer);
    }
    return true;
  }
}
registerProcessor('audio-processor', AudioProcessor);
`;

const blob = new Blob([workletCode], { type: 'application/javascript' });
const workletUrl = URL.createObjectURL(blob);

// State Variables
let websocket = null;
let audioContext = null;
let micStream = null;
let videoStream = null;
let workletNode = null;
let videoInterval = null;
let sessionActive = false;
let micMuted = false;
let videoMode = 'none'; // 'none', 'camera', 'screen'
let nextPlaybackTime = 0;
let activeSources = [];
let currentAssistantMessageDiv = null;
let currentAssistantTextNode = null;

let userAnalyser = null;
let geminiAnalyser = null;
let userVisualizerId = null;
let geminiVisualizerId = null;

// DOM Elements
const apiKeyInput = document.getElementById('api-key');
const toggleApiKeyBtn = document.getElementById('toggle-api-key');
const modelSelect = document.getElementById('model-select');
const voiceSelect = document.getElementById('voice-select');
const systemInstruction = document.getElementById('system-instruction');
const connectionStatusBadge = document.getElementById('connection-status');
const statusText = document.getElementById('status-text');
const connectBtn = document.getElementById('connect-btn');
const micToggleBtn = document.getElementById('mic-toggle');
const cameraToggleBtn = document.getElementById('camera-toggle');
const screenToggleBtn = document.getElementById('screen-toggle');
const localVideo = document.getElementById('local-video');
const videoPlaceholder = document.getElementById('video-placeholder');
const chatLog = document.getElementById('chat-log');
const clearLogBtn = document.getElementById('clear-log');
const userCanvas = document.getElementById('user-canvas');
const geminiCanvas = document.getElementById('gemini-canvas');

// Load API Key from localStorage
const savedKey = localStorage.getItem('gemini_live_api_key');
if (savedKey) {
  apiKeyInput.value = savedKey;
}

// Toggle API Key visibility
toggleApiKeyBtn.addEventListener('click', () => {
  const type = apiKeyInput.type === 'password' ? 'text' : 'password';
  apiKeyInput.type = type;
  const icon = toggleApiKeyBtn.querySelector('i');
  icon.className = type === 'password' ? 'fa-solid fa-eye' : 'fa-solid fa-eye-slash';
});

// Clear Chat Log
clearLogBtn.addEventListener('click', () => {
  chatLog.innerHTML = `
    <div class="system-message">
      <span class="timestamp">System</span>
      <p>Chat log cleared.</p>
    </div>
  `;
});

// Connect/Disconnect Button Handler
connectBtn.addEventListener('click', () => {
  if (sessionActive) {
    disconnectSession();
  } else {
    connectSession();
  }
});

// Connect Session
async function connectSession() {
  const apiKey = apiKeyInput.value.trim();
  if (!apiKey) {
    appendSystemMessage("Error: Please enter a Gemini API Key.");
    return;
  }
  
  // Save API key for convenience
  localStorage.setItem('gemini_live_api_key', apiKey);
  
  updateConnectionStatus('connecting');
  
  const model = modelSelect.value;
  const voice = voiceSelect.value;
  const instruction = systemInstruction.value.trim();
  
  // Establish WebSocket connection
  const wsUrl = `wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContent?key=${apiKey}`;
  
  try {
    websocket = new WebSocket(wsUrl);
  } catch (error) {
    console.error("Failed to create WebSocket: ", error);
    appendSystemMessage(`Error: Failed to connect WebSocket. ${error.message}`);
    updateConnectionStatus('disconnected');
    return;
  }
  
  websocket.onopen = async () => {
    console.log("WebSocket connection established");
    sessionActive = true;
    updateConnectionStatus('connected');
    
    // Initialize Web Audio (Mic capture and visualizers)
    try {
      await initAudio();
    } catch (err) {
      console.error("Failed to initialize audio: ", err);
      appendSystemMessage(`Warning: Audio initialization failed (${err.message}). Speech features may not work.`);
    }
    
    // Send Setup Message
    const setupMsg = {
      setup: {
        model: model,
        generation_config: {
          response_modalities: ["AUDIO"],
          speech_config: {
            voice_config: {
              prebuilt_voice_config: {
                voice_name: voice
              }
            }
          }
        }
      }
    };
    
    if (instruction) {
      setupMsg.setup.system_instruction = {
        parts: [{ text: instruction }]
      };
    }
    
    websocket.send(JSON.stringify(setupMsg));
    console.log("Setup message sent: ", setupMsg);
    appendSystemMessage("Session connected. You can start speaking now!");
  };
  
  websocket.onmessage = async (event) => {
    try {
      let data;
      if (event.data instanceof Blob) {
        const text = await event.data.text();
        data = JSON.parse(text);
      } else {
        data = JSON.parse(event.data);
      }
      
      if (data.serverContent) {
        const serverContent = data.serverContent;
        
        // Handle model response parts
        if (serverContent.modelTurn && serverContent.modelTurn.parts) {
          for (const part of serverContent.modelTurn.parts) {
            if (part.text) {
              handleAssistantText(part.text);
            }
            if (part.inlineData && part.inlineData.data) {
              handleAssistantAudio(part.inlineData.data);
            }
          }
        }
        
        // Handle Interruption (if user speaks while model is responding)
        if (serverContent.interrupted) {
          console.log("Model response interrupted by user");
          handleInterruption();
        }
        
        // Handle Turn Complete
        if (serverContent.turnComplete) {
          console.log("Turn complete");
          currentAssistantMessageDiv = null;
          currentAssistantTextNode = null;
        }
      }
    } catch (err) {
      console.error("Error parsing message: ", err);
    }
  };
  
  websocket.onerror = (error) => {
    console.error("WebSocket error: ", error);
    appendSystemMessage("Error: WebSocket connection error.");
  };
  
  websocket.onclose = (event) => {
    console.log(`WebSocket closed: code=${event.code}, reason=${event.reason}`);
    appendSystemMessage("Session disconnected.");
    disconnectSession();
  };
}

// Initialize Web Audio API
async function initAudio() {
  audioContext = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
  await audioContext.resume();
  
  // Setup Analysers
  userAnalyser = audioContext.createAnalyser();
  userAnalyser.fftSize = 256;
  
  geminiAnalyser = audioContext.createAnalyser();
  geminiAnalyser.fftSize = 256;
  geminiAnalyser.connect(audioContext.destination);
  
  // Start Microphone capture
  micStream = await navigator.mediaDevices.getUserMedia({
    audio: {
      echoCancellation: true,
      noiseSuppression: true,
      channelCount: 1,
      sampleRate: 16000
    }
  });
  
  const micSource = audioContext.createMediaStreamSource(micStream);
  micSource.connect(userAnalyser);
  
  // Load AudioWorklet
  await audioContext.audioWorklet.addModule(workletUrl);
  workletNode = new AudioWorkletNode(audioContext, 'audio-processor');
  
  workletNode.port.onmessage = (event) => {
    if (!sessionActive || micMuted) return;
    const int16Data = event.data;
    if (websocket && websocket.readyState === WebSocket.OPEN) {
      const base64Data = int16ToBase64(int16Data);
      const audioMsg = {
        realtimeInput: {
          mediaChunks: [
            {
              mimeType: "audio/pcm;rate=16000",
              data: base64Data
            }
          ]
        }
      };
      websocket.send(JSON.stringify(audioMsg));
    }
  };
  
  micSource.connect(workletNode);
  
  // Start visualizer loops
  startVisualizers();
}

// Convert Int16Array to Base64
function int16ToBase64(int16Array) {
  const buffer = int16Array.buffer;
  const bytes = new Uint8Array(buffer);
  let binary = '';
  const len = bytes.byteLength;
  for (let i = 0; i < len; i++) {
    binary += String.fromCharCode(bytes[i]);
  }
  return btoa(binary);
}

// Handle Gemini Transcription Text
function handleAssistantText(text) {
  if (!currentAssistantMessageDiv) {
    // Create new chat bubble for Gemini
    currentAssistantMessageDiv = document.createElement('div');
    currentAssistantMessageDiv.className = 'chat-bubble model';
    
    const sender = document.createElement('span');
    sender.className = 'chat-bubble-sender';
    sender.textContent = 'Gemini';
    currentAssistantMessageDiv.appendChild(sender);
    
    const p = document.createElement('p');
    currentAssistantMessageDiv.appendChild(p);
    
    chatLog.appendChild(currentAssistantMessageDiv);
    currentAssistantTextNode = p;
  }
  
  currentAssistantTextNode.textContent += text;
  chatLog.scrollTop = chatLog.scrollHeight;
}

// Play Gemini Voice Output
function handleAssistantAudio(base64Data) {
  if (!audioContext) return;
  
  try {
    const binaryString = atob(base64Data);
    const len = binaryString.length;
    const bytes = new Uint8Array(len);
    for (let i = 0; i < len; i++) {
      bytes[i] = binaryString.charCodeAt(i);
    }
    
    // Convert 16-bit PCM (signed short) to Float32
    const pcmData = new Int16Array(bytes.buffer);
    const float32Data = new Float32Array(pcmData.length);
    for (let i = 0; i < pcmData.length; i++) {
      float32Data[i] = pcmData[i] / 32768.0;
    }
    
    // Create an AudioBuffer (1 channel, 24000Hz output from Gemini)
    const audioBuffer = audioContext.createBuffer(1, float32Data.length, 24000);
    audioBuffer.copyToChannel(float32Data, 0);
    
    const source = audioContext.createBufferSource();
    source.buffer = audioBuffer;
    source.connect(geminiAnalyser);
    
    const now = audioContext.currentTime;
    if (nextPlaybackTime < now) {
      nextPlaybackTime = now;
    }
    
    source.start(nextPlaybackTime);
    activeSources.push(source);
    
    source.onended = () => {
      const idx = activeSources.indexOf(source);
      if (idx !== -1) {
        activeSources.splice(idx, 1);
      }
    };
    
    nextPlaybackTime += audioBuffer.duration;
  } catch (err) {
    console.error("Error playing back audio: ", err);
  }
}

// Stop current and queued playbacks
function handleInterruption() {
  if (audioContext) {
    nextPlaybackTime = audioContext.currentTime;
  }
  activeSources.forEach(src => {
    try {
      src.stop();
    } catch (e) {
      // ignore
    }
  });
  activeSources = [];
}

// Disconnect Session
function disconnectSession() {
  sessionActive = false;
  
  // Close WebSocket
  if (websocket) {
    if (websocket.readyState === WebSocket.OPEN || websocket.readyState === WebSocket.CONNECTING) {
      websocket.close();
    }
    websocket = null;
  }
  
  // Stop mic capture
  if (micStream) {
    micStream.getTracks().forEach(track => track.stop());
    micStream = null;
  }
  
  // Stop worklet node
  if (workletNode) {
    workletNode.disconnect();
    workletNode = null;
  }
  
  // Stop video capture
  stopVideoStream();
  
  // Handle audio context
  handleInterruption();
  if (audioContext) {
    try {
      audioContext.close();
    } catch (e) {
      console.error(e);
    }
    audioContext = null;
  }
  
  // Cancel visualizers animation frames
  if (userVisualizerId) {
    cancelAnimationFrame(userVisualizerId);
    userVisualizerId = null;
  }
  if (geminiVisualizerId) {
    cancelAnimationFrame(geminiVisualizerId);
    geminiVisualizerId = null;
  }
  
  // Reset analysers
  userAnalyser = null;
  geminiAnalyser = null;
  
  // Reset visualizer canvases
  clearCanvas(userCanvas);
  clearCanvas(geminiCanvas);
  
  // Reset UI
  updateConnectionStatus('disconnected');
  currentAssistantMessageDiv = null;
  currentAssistantTextNode = null;
}

// Start visualizer loops
function startVisualizers() {
  if (userAnalyser && userCanvas) {
    drawWaveform(userAnalyser, userCanvas, '#a78bfa', (id) => userVisualizerId = id);
  }
  if (geminiAnalyser && geminiCanvas) {
    drawWaveform(geminiAnalyser, geminiCanvas, '#22d3ee', (id) => geminiVisualizerId = id);
  }
}

// Draw Audio Waveform
function drawWaveform(analyser, canvas, color, setAnimId) {
  const ctx = canvas.getContext('2d');
  const bufferLength = analyser.frequencyBinCount;
  const dataArray = new Uint8Array(bufferLength);
  
  // Sizing
  canvas.width = canvas.parentElement.clientWidth || 300;
  canvas.height = canvas.parentElement.clientHeight || 55;
  
  function draw() {
    if (!sessionActive) return;
    const animId = requestAnimationFrame(draw);
    if (setAnimId) setAnimId(animId);
    
    analyser.getByteTimeDomainData(dataArray);
    
    ctx.fillStyle = 'rgba(15, 23, 42, 0.2)'; // Dark slate background matches theme
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    
    ctx.lineWidth = 2;
    ctx.strokeStyle = color;
    ctx.beginPath();
    
    const sliceWidth = canvas.width / bufferLength;
    let x = 0;
    
    for (let i = 0; i < bufferLength; i++) {
      const v = dataArray[i] / 128.0;
      const y = (v * canvas.height) / 2;
      
      if (i === 0) {
        ctx.moveTo(x, y);
      } else {
        ctx.lineTo(x, y);
      }
      x += sliceWidth;
    }
    
    ctx.lineTo(canvas.width, canvas.height / 2);
    ctx.stroke();
  }
  
  draw();
}

function clearCanvas(canvas) {
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, canvas.width, canvas.height);
}

// Microphone Mute Toggle
micToggleBtn.addEventListener('click', () => {
  if (!sessionActive) return;
  micMuted = !micMuted;
  
  const icon = micToggleBtn.querySelector('i');
  if (micMuted) {
    micToggleBtn.classList.add('muted');
    icon.className = 'fa-solid fa-microphone-slash';
    micToggleBtn.title = 'Unmute Microphone';
    appendSystemMessage("Microphone muted.");
  } else {
    micToggleBtn.classList.remove('muted');
    icon.className = 'fa-solid fa-microphone';
    micToggleBtn.title = 'Mute Microphone';
    appendSystemMessage("Microphone active.");
  }
});

// Camera Stream Toggle
cameraToggleBtn.addEventListener('click', async () => {
  if (!sessionActive) return;
  
  if (videoMode === 'camera') {
    stopVideoStream();
  } else {
    try {
      await startVideoStream('camera');
    } catch (err) {
      console.error("Failed to start camera: ", err);
      appendSystemMessage(`Error starting webcam: ${err.message}`);
    }
  }
});

// Screen Share Stream Toggle
screenToggleBtn.addEventListener('click', async () => {
  if (!sessionActive) return;
  
  if (videoMode === 'screen') {
    stopVideoStream();
  } else {
    try {
      await startVideoStream('screen');
    } catch (err) {
      console.error("Failed to start screen share: ", err);
      appendSystemMessage(`Error starting screen share: ${err.message}`);
    }
  }
});

// Start video source (webcam or screen share)
async function startVideoStream(mode) {
  stopVideoStream();
  
  videoMode = mode;
  
  try {
    if (mode === 'camera') {
      videoStream = await navigator.mediaDevices.getUserMedia({
        video: { width: 640, height: 480, frameRate: { max: 15 } }
      });
      cameraToggleBtn.classList.add('active');
      screenToggleBtn.classList.remove('active');
      document.getElementById('media-source-label').textContent = 'Camera';
      document.getElementById('media-source-label').classList.remove('hidden');
    } else if (mode === 'screen') {
      videoStream = await navigator.mediaDevices.getDisplayMedia({
        video: { width: 640, height: 480, frameRate: { max: 15 } }
      });
      screenToggleBtn.classList.add('active');
      cameraToggleBtn.classList.remove('active');
      document.getElementById('media-source-label').textContent = 'Screen';
      document.getElementById('media-source-label').classList.remove('hidden');
    }
    
    localVideo.srcObject = videoStream;
    localVideo.classList.remove('hidden');
    videoPlaceholder.classList.add('hidden');
    
    // Handle user ending stream via browser UI (e.g. stop sharing)
    videoStream.getVideoTracks()[0].onended = () => {
      stopVideoStream();
    };
    
    // Periodically capture frames to send to Gemini
    videoInterval = setInterval(sendVideoFrame, 1000);
    appendSystemMessage(`${mode === 'camera' ? 'Webcam' : 'Screen share'} feed started.`);
  } catch (err) {
    videoMode = 'none';
    throw err;
  }
}

// Stop video source
function stopVideoStream() {
  if (videoInterval) {
    clearInterval(videoInterval);
    videoInterval = null;
  }
  
  if (videoStream) {
    videoStream.getTracks().forEach(track => track.stop());
    videoStream = null;
  }
  
  videoMode = 'none';
  localVideo.srcObject = null;
  localVideo.classList.add('hidden');
  videoPlaceholder.classList.remove('hidden');
  document.getElementById('media-source-label').classList.add('hidden');
  
  cameraToggleBtn.classList.remove('active');
  screenToggleBtn.classList.remove('active');
}

// Capture frame and send via WebSocket
function sendVideoFrame() {
  if (!websocket || websocket.readyState !== WebSocket.OPEN) return;
  if (videoMode === 'none' || !videoStream) return;
  
  const canvas = document.getElementById('hidden-canvas');
  if (localVideo.videoWidth === 0 || localVideo.videoHeight === 0) return;
  
  canvas.width = 320; // Lower resolution for fast transmission
  canvas.height = (localVideo.videoHeight / localVideo.videoWidth) * canvas.width;
  
  const ctx = canvas.getContext('2d');
  ctx.drawImage(localVideo, 0, 0, canvas.width, canvas.height);
  
  try {
    const dataUrl = canvas.toDataURL('image/jpeg', 0.5); // 50% compression quality
    const base64Data = dataUrl.split(',')[1];
    
    const message = {
      realtimeInput: {
        mediaChunks: [
          {
            mimeType: "image/jpeg",
            data: base64Data
          }
        ]
      }
    };
    websocket.send(JSON.stringify(message));
  } catch (e) {
    console.error("Failed to capture or send video frame: ", e);
  }
}

// Update UI Connection States
function updateConnectionStatus(state) {
  // state: 'disconnected', 'connecting', 'connected'
  connectionStatusBadge.className = `status-badge ${state}`;
  
  if (state === 'disconnected') {
    statusText.textContent = 'Disconnected';
    connectBtn.textContent = 'Connect Session';
    connectBtn.className = 'dock-btn action-connect';
    const icon = document.createElement('i');
    icon.className = 'fa-solid fa-phone';
    connectBtn.prepend(icon);
    
    // Disable in-session controls
    micToggleBtn.disabled = true;
    cameraToggleBtn.disabled = true;
    screenToggleBtn.disabled = true;
    
    // Remove active styles from controls
    micToggleBtn.className = 'dock-btn icon-only';
    cameraToggleBtn.className = 'dock-btn icon-only';
    screenToggleBtn.className = 'dock-btn icon-only';
    const micIcon = micToggleBtn.querySelector('i');
    micIcon.className = 'fa-solid fa-microphone';
    
    // Enable inputs
    apiKeyInput.disabled = false;
    modelSelect.disabled = false;
    voiceSelect.disabled = false;
    systemInstruction.disabled = false;
  } else if (state === 'connecting') {
    statusText.textContent = 'Connecting...';
    connectBtn.textContent = 'Connecting...';
    connectBtn.disabled = true;
    
    // Disable inputs
    apiKeyInput.disabled = true;
    modelSelect.disabled = true;
    voiceSelect.disabled = true;
    systemInstruction.disabled = true;
  } else if (state === 'connected') {
    statusText.textContent = 'Connected';
    connectBtn.textContent = 'Disconnect Session';
    connectBtn.className = 'dock-btn action-disconnect';
    connectBtn.disabled = false;
    const icon = document.createElement('i');
    icon.className = 'fa-solid fa-phone-slash';
    connectBtn.prepend(icon);
    
    // Enable and set in-session controls
    micToggleBtn.disabled = false;
    cameraToggleBtn.disabled = false;
    screenToggleBtn.disabled = false;
    
    micToggleBtn.className = 'dock-btn icon-only active';
    micMuted = false;
    
    // Disable inputs
    apiKeyInput.disabled = true;
    modelSelect.disabled = true;
    voiceSelect.disabled = true;
    systemInstruction.disabled = true;
  }
}

// Log System Message
function appendSystemMessage(text) {
  const div = document.createElement('div');
  div.className = 'system-message';
  
  const span = document.createElement('span');
  span.className = 'timestamp';
  span.textContent = 'System';
  div.appendChild(span);
  
  const p = document.createElement('p');
  p.textContent = text;
  div.appendChild(p);
  
  chatLog.appendChild(div);
  chatLog.scrollTop = chatLog.scrollHeight;
}
