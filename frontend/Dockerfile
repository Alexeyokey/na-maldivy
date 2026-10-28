FROM node:22-alpine
WORKDIR /app
COPY --chown=node:node package.json server.mjs ./
COPY --chown=node:node dist ./dist
ENV HOST=0.0.0.0 PORT=5173 AGENT_URL=http://agent:8000 CHAT_MODE=api
USER node
EXPOSE 5173
CMD ["node", "server.mjs"]
