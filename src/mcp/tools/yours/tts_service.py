# 请安装 DashScope SDK 的最新版本
import os
import dashscope
import pyaudio
import time
import base64
import numpy as np
import threading

class TTSService:
    _instance = None
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = TTSService()
        return cls._instance

    def __init__(self):
        if TTSService._instance is not None:
            logger.error("尝试创建TTSService的多个实例")
            raise Exception("TTSService是单例类，请使用get_instance()获取实例")
        TTSService._instance = self
        
        self.p = pyaudio.PyAudio()
        dashscope.base_http_api_url = 'https://dashscope.aliyuncs.com/api/v1'
        
        # 创建音频流
        self.stream = self.p.open(format=pyaudio.paInt16,
                channels=1,
                rate=24000,
                output=True)
        
        self.api_key = "sk-590e702be8234993b65931583b71a69c"
        self.voice = "Cherry"
        self.language_type = "Chinese"
        self.play_queue = []
        self.is_playing = False
    
    def play_audio(self, text, voice=None, language_type=None):
        self.play_queue.append({
            "text": text,
            "voice": voice or self.voice,
            "language_type": language_type or self.language_type,
        })
        if not self.is_playing:
            self.is_playing = True
            self._play_next()
    
    def send_tts_state_changed(self, state):
        from src.application import Application
        self.app = Application.get_instance()
        if state == "idle":
            ## 恢复监听\
            self.app.schedule_command_nowait(self.app.start_auto_conversation)
        if state == "playing":
            ## 暂停监听
            if self.app.is_speaking():
                self.app.schedule_command_nowait(self.app.abort_speaking)
            if self.app.is_listening():
                self.app.schedule_command_nowait(self.app.stop_listening_manual)
        self.app._main_loop.create_task(self.app.plugins.notify_tts_state_changed(state))

    def _play_next(self):

        if self.play_queue:
            self.send_tts_state_changed("playing")
            audio_data = self.play_queue.pop(0)
            self._play(audio_data)
            self._play_next()
        else:
            self.is_playing = False
            self.send_tts_state_changed("idle")
    
    def _play(self, play_item):
        response = self.synthesize_speech(play_item)
        self.stream.start_stream()
        for chunk in response:
            if chunk.output is not None:
                audio = chunk.output.audio
                if audio.data is not None:
                    wav_bytes = base64.b64decode(audio.data)
                    audio_np = np.frombuffer(wav_bytes, dtype=np.int16)
                    # 直接播放音频数据
                    self.stream.write(audio_np.tobytes())
                if chunk.output.finish_reason == "stop":
                    print("finish at: {} ", chunk.output.audio.expires_at)
        time.sleep(0.8)

    def stop_play(self):
        self.play_queue.clear()
        self.is_playing = False
        self.stream.stop_stream()
    
    def synthesize_speech(self, play_item):
        text = play_item["text"]
        voice = play_item["voice"]
        language_type = play_item["language_type"]
        response = dashscope.MultiModalConversation.call(
            # 仅支持qwen-tts系列模型，请勿使用除此之外的其他模型
            model="qwen3-tts-flash",
            # 新加坡和北京地域的API Key不同。获取API Key：https://help.aliyun.com/zh/model-studio/get-api-key
            # 若没有配置环境变量，请用阿里云百炼API Key将下行替换为：api_key="sk-xxx"
            api_key=self.api_key,
            text=text,
            stream=True,
            voice=voice,
            language_type=language_type
        )
        return response

    def shutdown(self):
        self.stop_play()
        self.stream.close()
        self.p.terminate()