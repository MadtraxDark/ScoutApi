FROM scoutapiv2-import-fix
USER root
COPY src /app/src
RUN pip install --no-deps /app
