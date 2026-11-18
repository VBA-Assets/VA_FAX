# -*- coding: utf-8 -*-
"""送付書（兼受領書）の入力の画面と、［オプション］の「送付書」のタブの中身（F-36・F-35。Ver210）。

入力の画面は、［OK］で result に入力（CoverInput）を入れて閉じる。送付書のページを作って文書に
入れるのは、画面の側（app.py）が行う。送信者の事務所名などは、この画面では表示だけとし、
［ファイル］→［オプション］の「送付書」のタブ（SenderFrame）で変える。
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional

from core.cover_sheet import COURT_HONORIFIC, HONORIFICS, CoverInput, DocItem, Sender
from gui import theme

_VISIBLE_ROWS = 7          # 書類の一覧の、スクロールせずに見える行の数
_MIN_ROWS = 5              # 書類の一覧の行の数の、初めの最小


def _muted(parent: tk.Misc, text: str, **kwargs) -> ttk.Label:
    return ttk.Label(parent, text=text, foreground=theme.COLOR_MUTED, **kwargs)


class SenderFrame(ttk.Frame):
    """送信者（事務所名・住所・電話・FAX・弁護士名）の入力欄（［オプション］の「送付書」のタブ）。"""

    def __init__(self, master: tk.Misc, sender: Sender):
        super().__init__(master)
        _muted(self, "送付書の送信者の欄に書きます。空の項目は書きません。肩書（例：原告訴訟代理人"
                     "弁護士）は、送付書を作るときに文書ごとに入力します。",
               wraplength=440, justify="left").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        self._vars: dict[str, tk.StringVar] = {}
        rows = (("office", "事務所名："), ("tel", "電話番号："), ("fax", "FAX番号："), ("lawyer", "弁護士名："))
        for i, (name, label) in enumerate(rows[:1] + (("address", "住所："),) + rows[1:], start=1):
            ttk.Label(self, text=label).grid(row=i, column=0, sticky="nw", pady=3)
            if name == "address":
                self._address = tk.Text(self, width=40, height=2, wrap="none")
                self._address.insert("1.0", sender.address)
                self._address.grid(row=i, column=1, sticky="w", pady=3)
                continue
            var = tk.StringVar(value=getattr(sender, name))
            self._vars[name] = var
            ttk.Entry(self, textvariable=var, width=40 if name in ("office", "lawyer") else 20).grid(
                row=i, column=1, sticky="w", pady=3)
        _muted(self, "住所は2行まで書けます（例：1行目に郵便番号、2行目に所在地）。弁護士名が複数のときは、"
                     "「甲野太郎、丙川三郎」のように書きます。",
               wraplength=440, justify="left").grid(row=7, column=0, columnspan=2, sticky="w", pady=(4, 0))

    def result(self) -> Sender:
        address = self._address.get("1.0", "end").strip("\n")
        return Sender(address=address, **{name: var.get().strip() for name, var in self._vars.items()})


class CoverSheetDialog(tk.Toplevel):
    """送付書の入力の画面（F-36）。"""

    def __init__(self, master: tk.Misc, initial: CoverInput, sender: Sender,
                 rebuild_documents: Callable[[], list[DocItem]],
                 bring_to_front: Callable[[tk.Misc], None] = lambda _w: None):
        super().__init__(master)
        self.title("送付書")
        self.resizable(False, False)
        self.configure(bg=theme.COLOR_BG)
        self.result: Optional[CoverInput] = None
        self._rebuild_documents = rebuild_documents

        body = ttk.Frame(self, padding=(12, 12, 12, 0))
        body.pack(fill="both", expand=True)

        def entry(parent, var: tk.StringVar, width: int, row: int, column: int, **grid) -> ttk.Entry:
            widget = ttk.Entry(parent, textvariable=var, width=width)
            widget.grid(row=row, column=column, sticky="w", pady=2, **grid)
            return widget

        self.date_var = tk.StringVar(value=initial.date)
        top = ttk.Frame(body)
        top.pack(fill="x")
        ttk.Label(top, text="送信日：").grid(row=0, column=0, sticky="w")
        entry(top, self.date_var, 20, 0, 1)

        # ---- 宛先
        to = ttk.Labelframe(body, text="宛先", padding=8)
        to.pack(fill="x", pady=(8, 0))
        self.court_var = tk.StringVar(value=initial.court)
        self.court_fax_var = tk.StringVar(value=initial.court_fax)
        self.other_var = tk.StringVar(value=initial.other)
        self.other_fax_var = tk.StringVar(value=initial.other_fax)
        self.honorific_var = tk.StringVar(value=initial.other_honorific)
        ttk.Label(to, text="裁判所：").grid(row=0, column=0, sticky="w")
        entry(to, self.court_var, 40, 0, 1)
        ttk.Label(to, text=COURT_HONORIFIC).grid(row=0, column=2, sticky="w", padx=(4, 12))
        ttk.Label(to, text="FAX：").grid(row=0, column=3, sticky="w")
        entry(to, self.court_fax_var, 16, 0, 4)
        _muted(to, "例：東京地方裁判所民事第1部ろ係").grid(row=1, column=1, columnspan=4, sticky="w")
        ttk.Label(to, text="相手方：").grid(row=2, column=0, sticky="w", pady=(4, 0))
        entry(to, self.other_var, 40, 2, 1)
        ttk.Combobox(to, textvariable=self.honorific_var, values=HONORIFICS, width=4, state="readonly").grid(
            row=2, column=2, sticky="w", padx=(4, 12))
        ttk.Label(to, text="FAX：").grid(row=2, column=3, sticky="w")
        entry(to, self.other_fax_var, 16, 2, 4)
        _muted(to, "例：被告訴訟代理人弁護士　乙山次郎（受領書の欄の、押印する人の名前にも使います）").grid(
            row=3, column=1, columnspan=4, sticky="w")

        # ---- 送信者
        frm = ttk.Labelframe(body, text="送信者", padding=8)
        frm.pack(fill="x", pady=(8, 0))
        self.sender_title_var = tk.StringVar(value=initial.sender_title)
        ttk.Label(frm, text="肩書：").grid(row=0, column=0, sticky="w")
        entry(frm, self.sender_title_var, 30, 0, 1)
        _muted(frm, "例：原告訴訟代理人弁護士").grid(row=0, column=2, sticky="w", padx=(8, 0))
        shown = "　".join(s for s in (sender.office.strip(), sender.lawyer.strip()) if s)
        note = (f"{shown}（事務所名・住所・電話・FAX・弁護士名は、［ファイル］→［オプション］の「送付書」で変えます）"
                if shown else
                "事務所名・住所・電話・FAX・弁護士名は、［ファイル］→［オプション］の「送付書」で入力します。")
        _muted(frm, note, wraplength=560, justify="left").grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(4, 0))

        # ---- 事件
        case = ttk.Labelframe(body, text="事件", padding=8)
        case.pack(fill="x", pady=(8, 0))
        self.case_number_var = tk.StringVar(value=initial.case_number)
        self.case_name_var = tk.StringVar(value=initial.case_name)
        self.parties_var = tk.StringVar(value=initial.parties)
        for row, (label, var, width, example) in enumerate((
                ("事件番号：", self.case_number_var, 30, "例：令和8年（ワ）第123号"),
                ("事件名：", self.case_name_var, 30, "例：売買代金請求事件"),
                ("当事者：", self.parties_var, 50, "例：原告　甲山一郎　被告　乙川商事株式会社"))):
            ttk.Label(case, text=label).grid(row=row, column=0, sticky="w")
            entry(case, var, width, row, 1)
            _muted(case, example).grid(row=row, column=2, sticky="w", padx=(8, 0))

        # ---- 送付する書類
        docs = ttk.Labelframe(body, text="送付する書類", padding=8)
        docs.pack(fill="x", pady=(8, 0))
        head = ttk.Frame(docs)
        head.pack(fill="x")
        ttk.Label(head, text="書類", width=52, anchor="center").pack(side="left")
        ttk.Label(head, text="枚数", width=7, anchor="center").pack(side="left")
        holder = ttk.Frame(docs)
        holder.pack(fill="x")
        self._canvas = tk.Canvas(holder, highlightthickness=0, bg=theme.COLOR_BG, borderwidth=0)
        self._scrollbar = ttk.Scrollbar(holder, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=self._scrollbar.set)
        self._canvas.pack(side="left", fill="x", expand=True)
        self._scrollbar.pack(side="right", fill="y")
        self._rows_frame = ttk.Frame(self._canvas)
        self._canvas.create_window((0, 0), window=self._rows_frame, anchor="nw")
        self._rows_frame.bind("<Configure>", lambda _e: self._fit_rows())
        self._rows: list[tuple[tk.StringVar, tk.StringVar]] = []
        self._set_documents(initial.documents)
        buttons = ttk.Frame(docs)
        buttons.pack(fill="x", pady=(6, 0))
        theme.create_button(buttons, text="行を足す", command=self._add_empty_row).pack(side="left")
        theme.create_button(buttons, text="ファイルから作り直す", command=self._on_rebuild).pack(
            side="left", padx=(6, 0))
        _muted(buttons, "空の行は書きません。").pack(side="left", padx=(8, 0))

        # ---- 通信欄・受領書
        ttk.Label(body, text="通信欄：").pack(anchor="w", pady=(8, 0))
        self.message_text = tk.Text(body, width=70, height=3, wrap="char")
        self.message_text.insert("1.0", initial.message)
        self.message_text.pack(fill="x")
        self.receipt_var = tk.BooleanVar(value=initial.receipt)
        tk.Checkbutton(body, text="受領書の欄を付ける（相手方に、記名押印して返送してもらう欄）",
                       variable=self.receipt_var).pack(anchor="w", pady=(8, 0))

        button_row = ttk.Frame(self)
        button_row.pack(fill="x", padx=12, pady=12)
        theme.create_button(button_row, accent=True, text="OK", width=10, command=self._on_ok).pack(
            side="right")
        theme.create_button(button_row, text="キャンセル", width=10, command=self.destroy).pack(
            side="right", padx=(0, 6))
        _muted(button_row, "［OK］で、送付書を文書の先頭に入れます（作成済みのときは差し替えます）。").pack(side="left")

        self.transient(master)
        theme.place_over(self, master)
        self.grab_set()
        self.bind("<Escape>", lambda _e: self.destroy())
        bring_to_front(self)

    # ------------------------------------------------------------ 書類の一覧

    def _add_row(self, name: str = "", pages: str = "") -> None:
        name_var, pages_var = tk.StringVar(value=name), tk.StringVar(value=pages)
        row = len(self._rows)
        ttk.Entry(self._rows_frame, textvariable=name_var, width=52).grid(row=row, column=0, sticky="w", pady=1)
        ttk.Entry(self._rows_frame, textvariable=pages_var, width=7, justify="center").grid(
            row=row, column=1, sticky="w", padx=(4, 0), pady=1)
        self._rows.append((name_var, pages_var))

    def _add_empty_row(self) -> None:
        self._add_row()
        self.update_idletasks()
        self._fit_rows()
        self._canvas.yview_moveto(1.0)

    def _set_documents(self, documents: list[DocItem]) -> None:
        for child in self._rows_frame.winfo_children():
            child.destroy()
        self._rows = []
        for item in documents:
            self._add_row(item.name, item.pages)
        for _ in range(max(0, _MIN_ROWS - len(documents))):
            self._add_row()
        self._fit_rows()

    def _fit_rows(self) -> None:
        self._rows_frame.update_idletasks()
        width = self._rows_frame.winfo_reqwidth()
        height = self._rows_frame.winfo_reqheight()
        row_height = height / max(1, len(self._rows))
        visible = int(row_height * min(len(self._rows), _VISIBLE_ROWS))
        self._canvas.configure(width=width, height=visible, scrollregion=(0, 0, width, height))

    def _on_rebuild(self) -> None:
        self._set_documents(self._rebuild_documents())

    def documents(self) -> list[DocItem]:
        items = [DocItem(n.get().strip(), p.get().strip()) for n, p in self._rows]
        return [item for item in items if item.name or item.pages]

    # ------------------------------------------------------------ OK

    def current_input(self) -> CoverInput:
        return CoverInput(
            date=self.date_var.get().strip(),
            court=self.court_var.get().strip(), court_fax=self.court_fax_var.get().strip(),
            other=self.other_var.get().strip(), other_honorific=self.honorific_var.get(),
            other_fax=self.other_fax_var.get().strip(),
            sender_title=self.sender_title_var.get().strip(),
            case_number=self.case_number_var.get().strip(), case_name=self.case_name_var.get().strip(),
            parties=self.parties_var.get().strip(),
            documents=self.documents(),
            message=self.message_text.get("1.0", "end").strip("\n"),
            receipt=bool(self.receipt_var.get()),
        )

    def _on_ok(self) -> None:
        self.result = self.current_input()
        self.destroy()
