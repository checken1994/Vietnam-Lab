// Dedicated loopback-safe launcher for Next.js standalone server (F-02)
const path = require("path");

if (!process.env.HOSTNAME) {
  process.env.HOSTNAME = "127.0.0.1";
}
if (!process.env.PORT) {
  process.env.PORT = "3000";
}

const standaloneServer = path.resolve(__dirname, "..", ".next", "standalone", "server.js");
require(standaloneServer);
