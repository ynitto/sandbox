export const AgentLoopTurnCompletion = async () => ({
  event: async ({ event }) => {
    const executable = process.env.AGENT_LOOP_EXECUTABLE
    if (!executable) return
    const status = event.type === "session.idle"
      ? "complete"
      : event.type === "session.error" ? "failure-hint" : ""
    if (!status) return
    const child = Bun.spawn([
      executable, "hook-event", "--adapter", "opencode",
      "--status", status, "--native-event", event.type,
    ], { stdin: "ignore", stdout: "ignore", stderr: "ignore" })
    await child.exited
  },
})
