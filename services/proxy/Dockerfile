FROM nginx:1.27-alpine

RUN apk add --no-cache openssl

COPY nginx.conf /etc/nginx/templates/nginx.conf
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh && rm -f /etc/nginx/conf.d/default.conf

EXPOSE 80 443
ENTRYPOINT ["/entrypoint.sh"]
