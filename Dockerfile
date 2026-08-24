FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Face models for the web client's face login. Tolerated failure: an
# offline build still works, face login just reports unavailable.
RUN python kiosk/download_models.py || echo "face models skipped (no network?)"

# Database lives on a mounted volume so it survives container updates
ENV DATABASE_PATH=/data/social_credit.db

CMD ["python", "bot.py"]
