FROM node:20-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production
ENV PORT=3000
ENV HOSTNAME=0.0.0.0

COPY .next/standalone ./
COPY .next/static ./.next/static
COPY public ./public
COPY gateway.js ./gateway.js

EXPOSE 3000

CMD ["node", "gateway.js"]
