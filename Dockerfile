FROM python:3.13-slim

WORKDIR /app

# Install ffmpeg (required for audio/video processing)
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

# Copy and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the source code
COPY . .

# Run Flask (gunicorn) for health checks + the bot concurrently
CMD gunicorn app:app & python main.py
