# Python 3.10 official image
FROM python:3.10-slim

# System update aur ffmpeg install (pytgcalls ke liye zaroori)
RUN apt-get update && \
    apt-get install -y ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# Working directory set karo
WORKDIR /app

# Pehle sirf requirements.txt copy karo (caching ke liye)
COPY requirements.txt .

# Python dependencies install karo
RUN pip install --no-cache-dir -r requirements.txt

# Baki saari files copy karo
COPY . .

# Bot run karo
CMD ["python", "main.py"]
