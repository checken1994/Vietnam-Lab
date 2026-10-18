FROM node:20-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production
ENV LOOP_SCHEDULER_HOST=127.0.0.1
ENV LOOP_SCHEDULER_PORT=3030

COPY package.json ./
COPY index.js ./

EXPOSE 3030

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD node -e "fetch('http://127.0.0.1:3030/healthz').then(r => r.ok ? process.exit(0) : process.exit(1)).catch(() => process.exit(1))"

CMD ["node", "index.js"]
