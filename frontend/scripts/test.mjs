import { startVitest } from 'vitest/node'

const runner = await startVitest('test', ['src'], {run:true}, {configFile:false})
if (!runner) process.exitCode = 1
else {
  const failed = runner.state.getFiles().some(file => file.result?.state === 'fail')
  await runner.close()
  if (failed) process.exitCode = 1
}
