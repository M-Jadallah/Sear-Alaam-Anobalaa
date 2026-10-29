FROM node:22-bookworm-slim AS build
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend ./
RUN npm run build
FROM nginxinc/nginx-unprivileged:1.28-alpine
LABEL org.opencontainers.image.source="https://github.com/M-Jadallah/Sear-Alaam-Anobalaa"
COPY deploy/nginx.conf /etc/nginx/nginx.conf
COPY deploy/proxy_params /etc/nginx/proxy_params_siyar
COPY --from=build /src/dist /usr/share/nginx/html
USER 101
EXPOSE 8080
