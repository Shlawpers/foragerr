FROM python:3.11-alpine

# Set working directory
WORKDIR /app

# Install dependencies
RUN python -m pip install --upgrade pip

# Copy requirements first to leverage Docker caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY *.py ./

# Copy example config (user will mount their own config.yaml)
COPY config.example.yaml ./

# Create data directory for persistent storage
RUN mkdir -p /app/data /app/locks

# Set environment variables
ENV PYTHONUNBUFFERED=1

# Volume for persistent data (database, logs, config)
VOLUME ["/app/data"]

# Default command
CMD ["python", "watchlist-scheduler.py"]
