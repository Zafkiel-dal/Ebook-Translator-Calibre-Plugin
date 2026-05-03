"""Token History Viewer dialog for browsing token usage history."""

from functools import wraps

from qt.core import (  # type: ignore
    Qt, QDialog, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QTableView, QAbstractTableModel, QAbstractItemView,
    QModelIndex, QGroupBox, QFormLayout, QMenu, QCursor)

from calibre.utils.localization import _  # type: ignore

from .lib.token_usage import load_history, save_history, format_number
from .components import Footer, AlertMessage


load_translations()  # type: ignore


class TokenHistoryDialog(QDialog):
    """Dialog to display token usage history in a sortable table."""

    def __init__(self, parent=None):
        QDialog.__init__(self, parent)
        self.setWindowTitle(_('Token Usage History'))
        self.setMinimumWidth(900)
        self.setMinimumHeight(500)

        self.alert = AlertMessage(self)
        self.footer = Footer()

        layout = QVBoxLayout(self)
        layout.addWidget(self.summary_widget())
        layout.addWidget(self.table_widget(), 1)
        layout.addWidget(self.button_widget())
        layout.addWidget(self.footer)

    def summary_widget(self):
        """Show per-engine totals at the top."""
        history = load_history()
        totals = history.get("totals", {})

        widget = QGroupBox(_('Totals by Engine'))
        form = QFormLayout(widget)

        if not totals:
            form.addRow(QLabel(_('No token usage data yet.')))
        else:
            for engine_name, data in sorted(totals.items()):
                total = data.get("total_tokens", 0)
                count = data.get("count", 0)
                thinking = data.get("thinking_tokens", 0)
                label = _('{} — {} total tokens ({} runs)').format(
                    engine_name, format_number(total), count)
                if thinking > 0:
                    label += _(' — {} thinking').format(format_number(thinking))
                form.addRow(QLabel(label))

        return widget

    def table_widget(self):
        self.table_view = TokenHistoryTableView(self)
        self.table_view.setModel(TokenHistoryTableModel())
        return self.table_view

    def button_widget(self):
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        refresh_btn = QPushButton(_('Refresh'))
        refresh_btn.clicked.connect(self.refresh_data)
        layout.addWidget(refresh_btn)

        delete_btn = QPushButton(_('Delete Selected'))
        delete_btn.setDisabled(True)
        delete_btn.clicked.connect(self.delete_selected)
        self.delete_btn = delete_btn
        layout.addWidget(delete_btn)

        clear_btn = QPushButton(_('Clear All'))
        clear_btn.clicked.connect(self.clear_all)
        layout.addWidget(clear_btn)

        layout.addStretch(1)

        close_btn = QPushButton(_('Close'))
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)

        # Enable/disable delete button based on selection
        self.table_view.selectionModel().selectionChanged.connect(
            lambda: delete_btn.setDisabled(
                not self.table_view.selectionModel().hasSelection()))

        return widget

    def refresh_data(self):
        """Reload data from the JSON file."""
        model = self.table_view.model()
        if model:
            model.refresh()
        # Update footer with total
        history = load_history()
        all_entries = history.get("entries", [])
        total_tokens = sum(e.get("total_tokens", 0) for e in all_entries)
        # Clear old footer items and add new one
        footer_layout = self.footer.layout()
        for i in range(footer_layout.count()):
            item = footer_layout.itemAt(i)
            if item and item.widget():
                item.widget().deleteLater()
        footer_layout.insertWidget(
            0, QLabel(_('Total entries: {} — {} tokens').format(
                len(all_entries), format_number(total_tokens))))

    def delete_selected(self):
        """Delete selected entries from the history."""
        selection = self.table_view.selectionModel().selectedRows()
        if not selection:
            return
        action = self.alert.ask(
            _('Are you sure you want to delete the {} selected entry(ies)?')
            .format(len(selection)))
        if action != 'yes':
            return

        model = self.table_view.model()
        # Collect indices to delete (in reverse order to maintain correct indices)
        rows_to_delete = sorted(
            [index.row() for index in selection], reverse=True)
        history = load_history()
        entries = history.get("entries", [])
        for row in rows_to_delete:
            if row < len(entries):
                del entries[row]
        history["entries"] = entries

        # Recalculate totals
        totals = {}
        for entry in entries:
            engine = entry.get("engine", "Unknown")
            if engine not in totals:
                totals[engine] = {
                    "input_tokens": 0, "output_tokens": 0,
                    "total_tokens": 0, "thinking_tokens": 0, "count": 0,
                }
            totals[engine]["input_tokens"] += entry.get("input_tokens", 0)
            totals[engine]["output_tokens"] += entry.get("output_tokens", 0)
            totals[engine]["total_tokens"] += entry.get("total_tokens", 0)
            totals[engine]["thinking_tokens"] += entry.get("thinking_tokens", 0)
            totals[engine]["count"] += 1
        history["totals"] = totals

        save_history(history)
        model.refresh()
        self.delete_btn.setDisabled(True)

    def clear_all(self):
        """Clear all token history."""
        action = self.alert.ask(
            _('Are you sure you want to clear all token usage history?'))
        if action != 'yes':
            return
        history = {"entries": [], "totals": {}}
        save_history(history)
        model = self.table_view.model()
        if model:
            model.refresh()
        self.delete_btn.setDisabled(True)


class TokenHistoryTableView(QTableView):
    def __init__(self, parent=None):
        QTableView.__init__(self, parent)
        self.setSortingEnabled(True)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.verticalHeader().setVisible(False)
        self.horizontalHeader().setStretchLastSection(True)
        self.setAlternatingRowColors(True)

    def contextMenuEvent(self, event):
        menu = QMenu()
        menu.addAction(_('Delete'), self.parent().delete_selected)
        menu.exec_(QCursor.pos())


def update_model(func):
    """Decorator to emit layout changed signals around model mutations."""
    @wraps(func)
    def wrapper(self, *args):
        self.layoutAboutToBeChanged.emit()
        func(self, *args)
        self.layoutChanged.emit()
    return wrapper


class TokenHistoryTableModel(QAbstractTableModel):
    headers = [
        _('Date'), _('Engine'), _('Book'), _('Model'),
        _('Input'), _('Output'), _('Total'), _('Thinking'), _('Batch'),
    ]

    def __init__(self):
        QAbstractTableModel.__init__(self)
        self.refresh()

    def headerData(self, section, orientation, role):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal:
            return self.headers[section]
        return section

    def data(self, index, role):
        if not index.isValid():
            return None
        entry = self.entries[index.row()]
        col = index.column()

        if role == Qt.DisplayRole:
            if col == 0:  # Date
                raw = entry.get("date", "")
                return raw[:19].replace("T", " ")
            elif col == 1:  # Engine
                return entry.get("engine", "")
            elif col == 2:  # Book
                return entry.get("book", "")
            elif col == 3:  # Model
                return entry.get("model", "")
            elif col == 4:  # Input
                return format_number(entry.get("input_tokens", 0))
            elif col == 5:  # Output
                return format_number(entry.get("output_tokens", 0))
            elif col == 6:  # Total
                return format_number(entry.get("total_tokens", 0))
            elif col == 7:  # Thinking
                val = entry.get("thinking_tokens", 0)
                return format_number(val) if val else "-"
            elif col == 8:  # Batch
                return _('Yes') if entry.get("batch") else _('No')

        if role == Qt.UserRole:
            return entry.get("date", "")

        if role == Qt.TextAlignmentRole:
            if col >= 4:
                return int(Qt.AlignRight | Qt.AlignVCenter)
            return int(Qt.AlignLeft | Qt.AlignVCenter)

    @update_model
    def refresh(self):
        history = load_history()
        self.entries = history.get("entries", [])

    @update_model
    def sort(self, column, order):
        def sort_key(entry):
            col = column
            if col == 0:
                return entry.get("date", "")
            elif col == 1:
                return entry.get("engine", "")
            elif col == 2:
                return entry.get("book", "")
            elif col == 3:
                return entry.get("model", "")
            elif col == 4:
                return entry.get("input_tokens", 0)
            elif col == 5:
                return entry.get("output_tokens", 0)
            elif col == 6:
                return entry.get("total_tokens", 0)
            elif col == 7:
                return entry.get("thinking_tokens", 0)
            elif col == 8:
                return str(entry.get("batch", False))
            return ""
        reverse = order == Qt.DescendingOrder
        self.entries = sorted(self.entries, key=sort_key, reverse=reverse)

    def rowCount(self, parent=QModelIndex()):
        return len(self.entries)

    def columnCount(self, parent=QModelIndex()):
        return len(self.headers)
