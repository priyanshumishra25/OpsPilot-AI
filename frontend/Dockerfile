# syntax=docker/dockerfile:1.7
FROM node:24-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund
COPY . .
ARG VITE_API_BASE_URL=http://localhost:8000
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.29-alpine AS runtime
ARG VITE_API_BASE_URL=http://localhost:8000
COPY nginx.conf /etc/nginx/templates/default.conf.template
ENV API_ORIGIN=$VITE_API_BASE_URL
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
