import { createServer } from 'vite'
import config from '../vite.config.mjs'

const server = await createServer({ ...config, configFile: false })
await server.listen()
server.printUrls()
for (const event of ['SIGINT', 'SIGTERM']) {
  process.on(event, async () => { await server.close(); process.exit(0) })
}
