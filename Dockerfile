FROM node:20-alpine AS development-dependencies
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:20-alpine AS production-dependencies
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev

FROM node:20-alpine AS build
WORKDIR /app
ARG VITE_API_ORIGIN=__SAME_ORIGIN__
ENV VITE_API_ORIGIN=${VITE_API_ORIGIN}
COPY --from=development-dependencies /app/node_modules ./node_modules
COPY package.json package-lock.json react-router.config.ts tsconfig.json vite.config.ts ./
COPY app ./app
COPY public ./public
RUN npm run build

FROM node:20-alpine AS runtime
ENV NODE_ENV=production \
    HOST=0.0.0.0 \
    PORT=3000
WORKDIR /app
COPY --chown=node:node package.json package-lock.json ./
COPY --chown=node:node --from=production-dependencies /app/node_modules ./node_modules
COPY --chown=node:node --from=build /app/build ./build
USER node
EXPOSE 3000
CMD ["npm", "run", "start"]
