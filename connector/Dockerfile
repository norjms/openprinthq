# OpenPrintHQ local connector — Docker image
# Runs on the same LAN as your printers. Outbound-only; no ports to publish.
FROM node:22-alpine
WORKDIR /app
COPY package.json ./
COPY src ./src
# No dependencies to install — the agent uses only Node built-ins.
USER node
ENV NODE_ENV=production
ENTRYPOINT ["node", "src/agent.js"]
