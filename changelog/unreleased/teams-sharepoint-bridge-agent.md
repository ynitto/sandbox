### change-watch: Teams の依頼を Copilot アプリに提出するエージェントを足す

- teams-sharepoint-bridge-agent を追加する。SharePoint から同期された Teams の依頼 JSON を検証し、宛先表に従って GitLab の issue またはコミットで結果を返すよう Copilot アプリに依頼する。提出済みの依頼 ID は台帳に記録し、様式違反は JSON Lines のログに追記する。
