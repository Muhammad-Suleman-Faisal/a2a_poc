Set-Location E:\a2a-poc
$cmds = @(
 'python auth\main.py',
 'python agents\meeting_planner\main.py',
 'python agents\other_agents.py travel',
 'python agents\other_agents.py birthday',
 'python agents\other_agents.py content',
 'python client\client_agent.py'
)
foreach ($c in $cmds) {
  Start-Process powershell -ArgumentList '-NoExit','-Command',"Set-Location E:\a2a-poc; .\.venv\Scripts\Activate.ps1; $c"
  Start-Sleep 1
}
