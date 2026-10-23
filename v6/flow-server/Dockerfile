FROM node:20-alpine

WORKDIR /app

COPY package*.json ./
# @servicenow/sdk is a devDependency but invoked at runtime via npx
RUN npm install

COPY . .
RUN npm run build

ENV HOST=0.0.0.0
ENV PORT=3000

EXPOSE 3000

CMD ["node", "dist/server.js"]
