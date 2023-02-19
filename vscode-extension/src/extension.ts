import * as vscode from 'vscode';
import * as cp from 'child_process';

// This method is called when your extension is activated
// Your extension is activated the very first time the command is executed
export function activate(context: vscode.ExtensionContext) {
	context.subscriptions.push(vscode.commands.registerCommand("transitland-atlas-vscode-extension.createNewDmfrFile", async () => {
		const doc = await vscode.workspace.openTextDocument({ language: "json" });
		await vscode.window.showTextDocument(doc);
		await vscode.commands.executeCommand("editor.action.insertSnippet", { "name": "DMFR file" });
	}));

	context.subscriptions.push(vscode.commands.registerCommand("transitland-atlas-vscode-extension.formatDmfrFile", async () => {
		if (vscode.window.activeTextEditor) {
			const currentDmfr = vscode.window.activeTextEditor.document.fileName;
			if (vscode.window.activeTextEditor.document.isDirty) {
				vscode.window.showWarningMessage('Save the current file before running the Transitland DMFR format command');
			} else {
				cp.exec(`transitland dmfr format --save ${currentDmfr}`, (err, stdout, stderr) => {
					if (stdout.match("\[ERROR\]")) {
						const errorMessage = stdout.split("[ERROR] ")[1];
						vscode.window.showWarningMessage(errorMessage);
					} else {
						vscode.window.showInformationMessage('Successfully applied opinionated DMFR format');
					}
					if (err) {
						vscode.window.showWarningMessage('Error: ' + err);
					}
				});
				// const terminal = vscode.window.createTerminal('transitland-lib');
				// terminal.sendText(`transitland dmfr format --save ${currentDmfr}`);
			}
		} else {
			vscode.window.showWarningMessage('No DMFR file currently open');
		}
	}));
}

// This method is called when your extension is deactivated
export function deactivate() { }
