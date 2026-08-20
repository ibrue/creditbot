FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Database lives on a mounted volume so it survives container updates
ENV DATABASE_PATH=/data/social_credit.db

CMD ["python", "bot.py"]
