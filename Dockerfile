FROM python:3.11-slim

# Zeitzonen-Daten für den Scheduler (geplante Käufe zur richtigen lokalen Zeit)
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1
ENV DB_PATH=data/bitmaster.db
ENV LOG_FILE=data/bitmaster.log

# Arbeitsverzeichnis
WORKDIR /app

# Kopiere die `requirements.txt` ins Image
COPY requirements.txt /app/requirements.txt

# Installiere Abhängigkeiten
RUN pip install --no-cache-dir -r requirements.txt

# Kopiere den Python-Programmcode und die Web-Templates ins Image
COPY app.py /app/app.py
COPY templates/ /app/templates/
COPY static/ /app/static/

# Persistenz-Verzeichnis (DB + Logs) – als Volume gemountet
RUN mkdir -p /app/data

# Exponiere Port 5000
EXPOSE 5000

# Setze den Startbefehl
CMD ["python3", "app.py"]