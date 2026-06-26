FROM python:3.13-slim

WORKDIR /app

# Install system dependencies: ffmpeg + build tools (for compiling C extensions)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    gcc \
    g++ \
    make \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Upgrade pip, setuptools, wheel (prevents many build issues)
RUN pip install --upgrade pip setuptools wheel

# Copy and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .

# Run both services
CMD gunicorn app:app & python main.py
