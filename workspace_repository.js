'use strict';
const path = require('path');
const fs = require('fs');

// User-level settings must never connect an unrelated window to another game's jobs.
function resolveRepository(workspace, namespace, markers) {
  const folders = workspace.workspaceFolders || [];
  const valid = root => markers.every(marker => fs.existsSync(path.join(root, marker)));
  const inside = (root, folder) => {
    const relative = path.relative(folder, root);
    return relative === '' || (!path.isAbsolute(relative) && relative !== '..' && !relative.startsWith('..' + path.sep));
  };
  const choices = new Set();
  for (const folder of folders) {
    const config = workspace.getConfiguration(namespace, folder.uri);
    const setting = config.inspect('repository');
    const explicit = setting?.workspaceFolderValue ?? setting?.workspaceValue;
    if (explicit) {
      const root = path.resolve(folder.uri.fsPath, explicit);
      if (!folders.some(f => inside(root, f.uri.fsPath)) || !valid(root)) {
        throw new Error(namespace.toUpperCase() + ': Das konfigurierte Repository muss zum geöffneten Workspace gehören.');
      }
      choices.add(fs.realpathSync(root));
    } else if (valid(folder.uri.fsPath)) {
      choices.add(fs.realpathSync(folder.uri.fsPath));
    }
  }
  if (choices.size !== 1) throw new Error(namespace.toUpperCase() + ': Öffne den passenden Projekt-Workspace oder wähle dort genau ein Repository.');
  return [...choices][0];
}

module.exports = {resolveRepository};
