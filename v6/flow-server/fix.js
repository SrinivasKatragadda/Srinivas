const fs = require('fs');
let content = fs.readFileSync('src/examples/all_actions_test.json', 'utf8');
content = content.replace(/\{\{action\.[^}]+\}\}/g, '{{trigger.current.sys_id}}');
fs.writeFileSync('src/examples/all_actions_test.json', content);
console.log('Fixed');
