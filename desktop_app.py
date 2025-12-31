import os
import sys
import threading
import webview
import socket
import json
import shutil

# Add the current directory to sys.path
if getattr(sys, 'frozen', False):
    basedir = sys._MEIPASS
    sys.path.append(basedir)
    os.chdir(basedir)
else:
    basedir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(basedir)

from app import app, socketio

class JSApi:
    def save_file(self, filename, content_base64):
        """Open a save file dialog and save the content."""
        import base64
        
        file_types = ('PDF Files (*.pdf)', 'All files (*.*)')
        if filename.endswith('.tex'):
            file_types = ('LaTeX Files (*.tex)', 'All files (*.*)')
            
        result = webview.windows[0].create_file_dialog(
            webview.SAVE_DIALOG, 
            directory='', 
            save_filename=filename,
            file_types=file_types
        )
        
        if result:
            save_path = result
            if isinstance(result, (tuple, list)):
                if len(result) > 0:
                    save_path = result[0]
                else:
                    return {"status": "cancelled"}

            try:
                # content_base64 might contain the data URI prefix
                if ',' in content_base64:
                    content_base64 = content_base64.split(',')[1]
                
                file_content = base64.b64decode(content_base64)
                
                with open(save_path, 'wb') as f:
                    f.write(file_content)
                
                return {"status": "success", "path": save_path}
            except Exception as e:
                return {"status": "error", "message": str(e)}
        
        return {"status": "cancelled"}

def get_free_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(('localhost', 0))
    port = sock.getsockname()[1]
    sock.close()
    return port

def start_server(port):
    # Run the Flask app
    # socketio.run is used in the original app, so we use it here too
    # allow_unsafe_werkzeug=True is needed for some environments/versions
    # debug=False for production/desktop feel
    socketio.run(app, host='127.0.0.1', port=port, debug=False, allow_unsafe_werkzeug=True)

if __name__ == '__main__':
    port = get_free_port()
    
    # Start server in a separate thread
    t = threading.Thread(target=start_server, args=(port,))
    t.daemon = True
    t.start()
    
    api = JSApi()
    
    # Create the window
    window = webview.create_window(
        'BMS Selection Tool', 
        f'http://127.0.0.1:{port}', 
        width=1280, 
        height=800,
        js_api=api
    )
    
    # Start the webview GUI
    webview.start()
