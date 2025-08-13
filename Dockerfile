# 1. Start with the official Python image (slim = smaller size)
FROM python:3.11-slim

# 2. Install build tools and psycopg2 dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# 3. Set environment variables to control Python behavior
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# 4. Set the working directory inside the container
WORKDIR /app

# 5. Copy and install Python dependencies
COPY requirements.txt /app/
RUN pip install --upgrade pip
RUN pip install -r requirements.txt

# 6. Copy the rest of the Django project code into the container
COPY . /app/

# 7. Create log directory for gunicorn and celery
RUN mkdir -p \
    /var/log/app/django_server/django \
    /var/log/app/django_server/gunicorn \
    /var/log/app/celery \
    && chmod -R 755 /var/log/app
