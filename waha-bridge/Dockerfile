# WhatsApp Bridge — always-on container.
# Runs the bridge 24/7 on any Docker host so WhatsApp no longer depends on the
# office PC being switched on.
FROM node:20-slim

WORKDIR /app

# Install only what the bridge needs
COPY package*.json ./
RUN npm install --omit=dev

COPY bridge.js ./

# The WhatsApp login is stored here. Mount a PERSISTENT volume at /app/session
# so a restart does NOT require re-scanning the QR code.
VOLUME ["/app/session"]

# These can be overridden by the host's environment settings.
ENV VERCEL_URL=https://kgirdharlal-recruitment.vercel.app
ENV BRIDGE_API_KEY=kgirdharlal-bridge-secret

# Local health check endpoint
EXPOSE 3001

CMD ["node", "bridge.js"]
