FROM debian:stable-slim
COPY install-packages.sh .
RUN ./install-packages.sh
USER bob
WORKDIR /home/bob
