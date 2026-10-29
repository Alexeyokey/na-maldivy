# Frontend-directory build context; the UI uses only Node built-ins.
FROM node:22.23.4-alpine3.24@sha256:fe4f6d74e20370acb04ad889077d14d0f64a9c8ea6a42bc60cddf1e844699914 AS assets
WORKDIR /source
COPY package.json server.mjs ./
COPY dist ./dist
RUN node --check server.mjs \
    && node --check dist/api.js \
    && node --check dist/api-app.js \
    && node --check dist/api-model.js \
    && node --check dist/message-format.js \
    && node --check dist/trip-presentation.js

FROM node:22.23.4-alpine3.24@sha256:fe4f6d74e20370acb04ad889077d14d0f64a9c8ea6a42bc60cddf1e844699914 AS runtime
WORKDIR /app
COPY --from=assets /source/package.json /source/server.mjs ./
COPY --from=assets /source/dist ./dist
ENV HOST=0.0.0.0 PORT=8080 AGENT_URL=http://agent:8000 CHAT_MODE=api
USER 1000:1000
EXPOSE 8080
HEALTHCHECK --interval=5s --timeout=3s --start-period=5s --retries=10 \
    CMD ["node", "--input-type=module", "-e", "const r=await fetch('http://127.0.0.1:'+(process.env.PORT||8080)+'/health'); if(!r.ok)process.exit(1)"]
CMD ["node", "server.mjs"]
