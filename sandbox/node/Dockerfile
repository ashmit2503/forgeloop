FROM node:22.16.0-bookworm-slim

RUN mkdir -p /workspace \
    && chown node:node /workspace

USER node
WORKDIR /workspace
CMD ["tail", "-f", "/dev/null"]
