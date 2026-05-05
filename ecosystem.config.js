module.exports = {
  apps: [
    {
      name: "world-cup-bot",
      cwd: __dirname,
      script: ".venv/bin/python",
      args: "-m world_cup_bot.bot",
      exec_interpreter: "none",
      instances: 1,
      autorestart: true,
      watch: false,
      max_restarts: 10,
      restart_delay: 5000,
      kill_timeout: 10000,
      env: {
        BOT_ENV: "development",
        LOG_LEVEL: "INFO",
      },
      env_production: {
        BOT_ENV: "production",
        LOG_LEVEL: "INFO",
      },
    },
  ],
};
