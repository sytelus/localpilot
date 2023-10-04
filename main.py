import requests
import threading
import subprocess
import os
import sys

import config
import utils

def download_model(model_name):
    models_download_path = utils.full_path(config.model_folder, create=True)
    model_config = config.models[model_name]
    if model_config['type'] == 'local':
        model_path = utils.full_path(os.path.join(models_download_path, model_name), create=True)
        if utils.is_directory_empty(model_path):
            url = model_config['url']
            print(f"Downloading {model_name} from {url}...")
            utils.download_file(url, os.path.join(model_path, model_config['filename']))
        else:
            print(f"Using {model_name} found in {model_path}.")

def select_model(model_name):
    try:
        response = requests.post(
            "http://localhost:5001/set_target", json={"target": model_name})
        if response.status_code == 200:
            print(f"Successfully sent selection: {model_name}.")
        else:
            print(f"Failed to send selection. Server responded with: {response.status_code}.")
    except requests.RequestException as e:
        print(f"Failed to send selection. Error: {e}.")

def run_server():
    subprocess.run(['python', 'proxy.py'])

if __name__ == '__main__':
    current_model = config.current_model
    download_model(current_model)

    print("Running server...")
    server_thread = threading.Thread(target=run_server)
    server_thread.start()

    select_model(current_model)

    input("Press Enter to exit.")