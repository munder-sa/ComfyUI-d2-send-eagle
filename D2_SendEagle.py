import json
import os
import shutil
import numpy as np
import yaml
import subprocess
from pathlib import Path
from typing import Dict, Optional, Literal

from PIL import Image
from PIL.PngImagePlugin import PngInfo
from datetime import datetime

import folder_paths

from .modules.util import util
from .modules.params_extractor import ParamsExtractor
from .modules.eagle_api import EagleAPI

from .my_types import TNodeParams, TGenInfo, D2_TD2Pipe, TConfig

FORCE_WRITE_PROMPT = False


class D2_SendEagle:
    # クラス変数として EagleAPI を保持（全インスタンスで共有）
    _eagle_api: Optional[EagleAPI] = None

    @classmethod
    def _get_eagle_api(cls) -> EagleAPI:
        """EagleAPI シングルトンを取得"""
        if cls._eagle_api is None:
            config = cls._load_config()
            token = config.get("eagle_api_token", "")
            cls._eagle_api = EagleAPI(token=token)
        return cls._eagle_api

    def __init__(self):
        self.output_dir = folder_paths.get_output_directory()
        self.type = "output"
        self.output_folder = ""
        self.subfolder_name = ""
        self.config = self._load_config()

    # #########################
    # 設定ファイルを読み込む
    @classmethod
    def _load_config(cls) -> TConfig:
        """config.yaml または config.org.yaml を読み込む"""
        base_dir = Path(__file__).resolve().parent
        config_file = base_dir / "config.yaml"
        config_org = base_dir / "config.org.yaml"

        # ユーザー設定がなければオリジナル設定をコピーする
        if not os.path.exists(config_file):
            shutil.copy2(config_org, config_file)

        with open(config_file, "r", encoding="utf-8") as file:
            config = yaml.safe_load(file)

        # トークンのクリーンアップ（改行、スペース、引用符を除去）
        if "eagle_api_token" in config and config["eagle_api_token"]:
            config["eagle_api_token"] = (
                str(config["eagle_api_token"]).strip().replace('"', "").replace("'", "")
            )

        return config

    # #########################
    # ブリッジディレクトリの (WSLパス, Windowsパス) を取得
    def _resolve_bridge_dir(self) -> tuple[str, str]:
        """ブリッジディレクトリの (WSLパス, Windowsパス) を返す。

        優先順位:
        1. 環境変数 EAGLE_BRIDGE_DIR (WSLパスで指定)
        2. config.yaml の bridge_dir
        3. wslpath で $USERPROFILE/EagleBridge を自動変換
        4. フォールバック: /tmp/EagleBridge (Windowsパスなし)
        """
        # 1. 環境変数から取得
        env_path = os.environ.get("EAGLE_BRIDGE_DIR", "")
        if env_path:
            win_path = util.to_windows_path(env_path)
            if win_path:
                return env_path, win_path

        # 2. config.yaml から取得
        config_bridge_dir = self.config.get("bridge_dir", "")
        if config_bridge_dir:
            # /mnt/ で始まらない場合は Windows パスに変換できない可能性を警告
            if not config_bridge_dir.startswith("/mnt/"):
                print(  # noqa: T201
                    f"[Eagle API] WARNING: bridge_dir={config_bridge_dir!r} は /mnt/c/ 等の"
                    " WSL共有パスではありません。Windowsから参照できるパスを設定してください。"
                )
            win_path = util.to_windows_path(config_bridge_dir)
            if win_path:
                return config_bridge_dir, win_path
            else:
                print(  # noqa: T201
                    f"[Eagle API] ERROR: bridge_dir={config_bridge_dir!r} を Windows パスに"
                    " 変換できませんでした。config.yaml で /mnt/c/... 形式のパスを指定してください。"
                )

        # 3. wslpath で $USERPROFILE/EagleBridge を自動変換
        try:
            userprofile = os.environ.get("USERPROFILE", "")
            if userprofile:
                result = subprocess.run(
                    ["wslpath", "-u", userprofile],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                userprofile_wsl = result.stdout.strip()
                if userprofile_wsl:
                    wsl_path = f"{userprofile_wsl}/EagleBridge"
                    win_path = util.to_windows_path(wsl_path)
                    if win_path:
                        return wsl_path, win_path
        except Exception:
            pass

        # 4. フォールバック
        print(  # noqa: T201
            "[Eagle API] ERROR: Windows パスが解決できませんでした。"
            " config.yaml の bridge_dir に /mnt/c/Users/.../EagleBridge 等の"
            " WSL共有パスを指定してください。"
        )
        return "/tmp/EagleBridge", ""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE",),
                "format": (["webp", "png", "jpeg"],),
                # webpの時に可逆（lossless）不可逆（lossy）どちらにするか
                "lossless_webp": (
                    "BOOLEAN",
                    {"default": True, "label_on": "lossless", "label_off": "lossy"},
                ),
                # webp lossy または jpeg の時の圧縮率
                "compression": (
                    "INT",
                    {"default": 90, "min": 1, "max": 100, "step": 1},
                ),
                # プロンプトやモデルをEagleタグに保存するか
                "save_tags": (
                    [
                        "None",
                        "Prompt + Checkpoint",
                        "Prompt",
                        "Checkpoint",
                    ],
                ),
                # 保存するファイル名
                "filename_template": (
                    "STRING",
                    {"multiline": False, "default": "{model}-{seed}"},
                ),
                # Eagleフォルダ
                "eagle_folder": ("STRING", {"default": ""}),
                # プレビュー表示するか
                "preview": (
                    "BOOLEAN",
                    {"default": True, "label_on": "ON", "label_off": "OFF"},
                ),
            },
            "optional": {
                # ポジティブプロンプト
                "positive": (
                    "STRING",
                    {"forceInput": True, "multiline": True},
                ),
                # ネガティブプロンプト
                "negative": (
                    "STRING",
                    {"forceInput": True, "multiline": True},
                ),
                # その他メモ
                "memo_text": (
                    "STRING",
                    {"multiline": True},
                ),
                "d2_pipe": ("D2_TD2Pipe",),
            },
            "hidden": {"prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO"},
        }

    RETURN_TYPES = ("STRING", "STRING", "IMAGE")
    RETURN_NAMES = (
        "positive",
        "negative",
        "IMAGE",
    )
    FUNCTION = "add_item"
    OUTPUT_NODE = True
    CATEGORY = "D2"

    # ######################
    # ノード実行で呼ばれる関数
    # Eagleに画像を送る
    def add_item(
        self,
        images,
        format="webp",
        lossless_webp=False,
        save_tags="None",
        filename_template="{model}-{width}-{height}-{seed}",
        eagle_folder="",
        compression=80,
        positive="",
        negative="",
        preview=True,
        memo_text="",
        d2_pipe: Optional[D2_TD2Pipe] = None,
        prompt: Optional[Dict] = None,
        extra_pnginfo: Optional[Dict] = None,
    ):
        self.output_folder, self.subfolder_name = self.get_output_folder()

        image_results = list()
        eagle_payloads = list()
        params = TNodeParams(
            format=format,
            lossless_webp=lossless_webp,
            save_tags=save_tags,
            filename_template=filename_template,
            eagle_folder=eagle_folder,
            compression=compression,
            positive=self.__class__.get_prompt_value("positive", positive, d2_pipe),
            negative=self.__class__.get_prompt_value("negative", negative, d2_pipe),
            memo_text=memo_text,
            prompt=prompt,
            extra_pnginfo=extra_pnginfo,
        )

        for image in images:
            img_result, eagle_payload = self.create_image_object(image, params, d2_pipe)
            image_results.append(img_result)
            eagle_payloads.append(eagle_payload)

        if preview:
            return {
                "ui": {"images": image_results, "d2_send_eagle": eagle_payloads},
                "result": (
                    params["positive"],
                    params["negative"],
                    images,
                ),
            }

        return {
            "ui": {"d2_send_eagle": eagle_payloads},
            "result": (
                params["positive"],
                params["negative"],
                images,
            ),
        }

    @classmethod
    def get_prompt_value(
        cls,
        mode: Literal["positive", "negative"],
        value: Optional[str],
        d2_pipe: Optional[D2_TD2Pipe],
    ) -> str:
        if value:
            return value
        if d2_pipe is not None and getattr(d2_pipe, mode) is not None:
            return getattr(d2_pipe, mode)
        return ""

    # ######################
    # イメージオブジェクトを作成
    def create_image_object(
        self, image, params: TNodeParams, d2_pipe: D2_TD2Pipe | None
    ) -> tuple[dict, dict]:
        normalized_pixels = 255.0 * image.cpu().numpy()
        img = Image.fromarray(np.clip(normalized_pixels, 0, 255).astype(np.uint8))

        # 生成パラメータ整理
        paramsExtractor = self.create_generate_params(img, params, d2_pipe)
        # 必要な生成パラメーターをまとめたもの
        gen_info = paramsExtractor.gen_info
        # EagleやPNGInfo用に整形したもの
        formated_info = paramsExtractor.format_info(params["memo_text"])

        # print("generate_params", gen_info)
        # print("format_info", formated_info)

        # 画像をローカルに保存
        file_name, file_full_path = self.save_image(
            img, params, gen_info, formated_info
        )

        # ブリッジディレクトリの取得（動的解決）
        bridge_wsl, bridge_win = self._resolve_bridge_dir()
        os.makedirs(bridge_wsl, exist_ok=True)

        # ブリッジディレクトリの事前クリーンアップ
        try:
            for old_file in os.listdir(bridge_wsl):
                try:
                    os.remove(os.path.join(bridge_wsl, old_file))
                except Exception:
                    pass
        except Exception:
            pass

        # ファイルをブリッジディレクトリにコピー
        shutil.copy2(file_full_path, os.path.join(bridge_wsl, file_name))

        # Eagle へ送信
        tags = self.get_tags(params, gen_info)
        folder_id = None
        if params["eagle_folder"]:
            result_id = self._get_eagle_api().find_or_create_folder(
                params["eagle_folder"]
            )
            folder_id = result_id if result_id else None

        # folder_id の検証
        print(  # noqa: T201
            f"DEBUG: eagle_folder={params['eagle_folder']!r}, folder_id={folder_id!r}"
        )
        if params["eagle_folder"] and not folder_id:
            print(  # noqa: T201
                "[Eagle API] WARNING: folder_id が空です。フォルダが見つからないか作成に失敗した可能性があります"
            )

        # Windowsパスが取得できた場合のみ Eagle API を呼び出す
        item = {
            "path": bridge_win.rstrip("\\") + "\\" + file_name if bridge_win else "",
            "name": file_name,
            "tags": tags,
            "annotation": formated_info,
        }

        # item["path"] のパス検証
        print(f"DEBUG: bridge_wsl={bridge_wsl!r}, bridge_win={bridge_win!r}")  # noqa: T201
        print(f"DEBUG: item path={item['path']!r}")  # noqa: T201

        # 送信前バリデーション: 空パス or /mnt/ パス（Windows変換失敗）は送信中止
        if not item["path"]:
            print(  # noqa: T201
                "[Eagle API] ERROR: item path が空です。Eagle へのリクエストを中止します。"
                " config.yaml の bridge_dir に /mnt/c/... 形式のパスを指定してください。"
            )
        elif item["path"].startswith("/mnt/"):
            print(  # noqa: T201
                f"[Eagle API] ERROR: item path={item['path']!r} が Windows パスに変換されていません。"
                " Eagle へのリクエストを中止します。"
                " config.yaml の bridge_dir に /mnt/c/... 形式のパスを指定してください。"
            )
        else:
            self._get_eagle_api().add_item_from_path(data=item, folder_id=folder_id)

        eagle_payload = {
            "filename": file_name,
            "subfolder": self.subfolder_name,
            "type": self.type,
            "tags": tags,
            "annotation": formated_info,
            "eagle_folder": params["eagle_folder"],
        }

        image_result = {
            "filename": file_name,
            "subfolder": self.subfolder_name,
            "type": self.type,
        }

        return image_result, eagle_payload

    # ######################
    # 登録タグを取得
    def get_tags(self, params: TNodeParams, gen_info: TGenInfo) -> list:
        if params["save_tags"] == "Prompt + Checkpoint":
            return [*util.get_prompt_tags(gen_info["positive"]), gen_info["model_name"]]

        elif params["save_tags"] == "Prompt":
            return util.get_prompt_tags(gen_info["positive"])

        elif params["save_tags"] == "Checkpoint":
            return [gen_info["model_name"]]

        return []

    # ######################
    # 画像をローカルに保存
    def save_image(
        self, img, params: TNodeParams, gen_info: TGenInfo, formated_info: str
    ):
        file_name = ""
        file_full_path = ""

        if params["format"] == "webp":
            # Save webp image file
            file_name = self.get_filename(params["filename_template"], "webp", gen_info)
            file_full_path = os.path.join(self.output_folder, file_name)

            exif = util.get_exif_from_prompt(
                img, formated_info, params["extra_pnginfo"], params["prompt"]
            )

            img.save(
                file_full_path,
                quality=params["compression"],
                exif=exif,
                lossless=params["lossless_webp"],
            )

        elif params["format"] == "jpeg":
            # Save jpeg image file
            file_name = self.get_filename(params["filename_template"], "jpeg", gen_info)
            file_full_path = os.path.join(self.output_folder, file_name)

            exif = util.get_exif_from_prompt(
                img, formated_info, params["extra_pnginfo"], params["prompt"]
            )

            img.save(
                file_full_path,
                quality=params["compression"],
                optimize=True,
                exif=exif,
            )

        else:
            # Save png image file
            file_name = self.get_filename(params["filename_template"], "png", gen_info)
            file_full_path = os.path.join(self.output_folder, file_name)

            metadata = PngInfo()

            if params["prompt"] is not None:
                metadata.add_text("prompt", json.dumps(params["prompt"]))
            if params["extra_pnginfo"] is not None:
                for x in params["extra_pnginfo"]:
                    metadata.add_text(x, json.dumps(params["extra_pnginfo"][x]))

            img.save(file_full_path, pnginfo=metadata, compress_level=4)

        return file_name, file_full_path

    # ######################
    # 画像保存パスを取得
    def get_output_folder(self):
        subfolder_name = datetime.now().strftime("%Y-%m-%d")

        # 画像保存用フォルダが無ければ作成
        output_folder = os.path.join(self.output_dir, subfolder_name)

        if not os.path.exists(output_folder):
            os.makedirs(output_folder)

        return output_folder, subfolder_name

    # ######################
    # 生成パラメーターを取得
    def create_generate_params(
        self, img, params: TNodeParams, d2_pipe: D2_TD2Pipe | None
    ) -> ParamsExtractor:
        # print("[SendEagle] create_generate_params - ", params )
        paramsExtractor = ParamsExtractor(params)
        paramsExtractor.gen_info["width"] = img.width
        paramsExtractor.gen_info["height"] = img.height

        # pipe が指定されていればその値を入力する
        if d2_pipe is not None:
            if d2_pipe.steps is not None:
                paramsExtractor.gen_info["steps"] = d2_pipe.steps
            if d2_pipe.sampler_name:
                paramsExtractor.gen_info["sampler_name"] = d2_pipe.sampler_name
            if d2_pipe.scheduler:
                paramsExtractor.gen_info["scheduler"] = d2_pipe.scheduler
            if d2_pipe.cfg is not None:
                paramsExtractor.gen_info["cfg"] = d2_pipe.cfg
            if d2_pipe.seed is not None:
                paramsExtractor.gen_info["seed"] = d2_pipe.seed
            if d2_pipe.ckpt_name:
                paramsExtractor.gen_info["model_name"] = d2_pipe.ckpt_name.replace(
                    "\\", "__"
                )

        return paramsExtractor

    # ######################
    # ファイルネームを取得
    def get_filename(self, template: str, ext: str, gen_info: TGenInfo) -> str:
        base = template.format(
            width=gen_info["width"],
            height=gen_info["height"],
            model=gen_info["model_name"],
            steps=gen_info["steps"],
            seed=gen_info["seed"],
        )

        return f"{util.get_datetime_str_msec()}-{base}.{ext}"
