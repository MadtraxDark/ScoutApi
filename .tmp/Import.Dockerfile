FROM scoutapiv2-api
COPY src /app/src
RUN pip install --no-deps /app
