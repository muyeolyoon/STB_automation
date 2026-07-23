from obswebsocket import obsws, requests

class OBSRecorder:
    def __init__(self, host="localhost", port=4444, password="123456"):
        self.ws = obsws(host, port, password)
        self.connected = False

    def connect(self):
        if not self.connected:
            self.ws.connect()
            self.connected = True

    def disconnect(self):
        if self.connected:
            self.ws.disconnect()
            self.connected = False

    def start_recording(self):
        if self.connected:
            self.ws.call(requests.StartRecording())

    def stop_recording(self):
        if self.connected:
            self.ws.call(requests.StopRecording())
