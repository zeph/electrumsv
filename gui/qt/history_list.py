from electrumsv.util import format_mnee_atomic # Make sure this is imported
from electrumsv.wallet import HistoryLine # Import HistoryLine if needed for comparison/type hints

# --- _ItemModel definition ---
class _ItemModel(QAbstractItemModel):
    # __init__, set_column_names remain mostly the same
    def __init__(self, parent: Any, column_names: List[str]) -> None:
        super().__init__(parent)
        self._view = parent
        self._logger = logger # Assume logger is defined globally or passed
        self._column_names = column_names
        self._account_id: Optional[int] = None
        self._data: List[Dict[str, Any]] = [] # Explicitly type as list of dicts

    def set_column_names(self, column_names: List[str]) -> None:
        self._column_names = column_names[:]

    def set_column_name(self, column_index: int, column_name: str) -> None:
        self._column_names[column_index] = column_name

    def set_data(self, account_id: Optional[int], data: List[dict]) -> None: # Changed data type
        self.beginResetModel()
        self._account_id = account_id
        self._data = data # Now expects list of dicts
        self.endResetModel()

    # ... (existing _get_data_line, _add_line, remove_row, add_line)
    # Note: _add_line might need adjustment if we insert MNEE items individually later

    # ... (existing invalidate_column, invalidate_row)

    # Overridden methods:

    def columnCount(self, model_index: QModelIndex) -> int:
        return len(self._column_names)

    def data(self, model_index: QModelIndex, role: int) -> Any:
        if not model_index.isValid(): return None # Check validity early
        # Account ID check might be less relevant if data is passed directly
        # if self._view._account_id != self._account_id: return None

        row = model_index.row()
        column = model_index.column()
        # Bounds checks
        if row >= len(self._data): 
            # self._logger.warning(f"data() row index {row} out of bounds ({len(self._data)})")
            return None
        if column >= len(self._column_names): 
            # self._logger.warning(f"data() column index {column} out of bounds ({len(self._column_names)})")
            return None

        try:
            item = self._data[row] # Item is now a dictionary
        except IndexError:
            # self._logger.error(f"IndexError accessing self._data[{row}]")
            return None
            
        item_type = item.get('type', 'bsv') # Default to 'bsv' if type is missing

        # --- QT_SORT_ROLE --- 
        if role == QT_SORT_ROLE:
            height = item.get('height', 0)
            timestamp = item.get('timestamp')
            # Ensure timestamp is numeric for comparison or None
            if isinstance(timestamp, str):
                try: timestamp = int(timestamp) 
                except ValueError: timestamp = None
                
            primary_sort = timestamp if timestamp is not None and height > 0 else float('inf')
            secondary_sort = height if height > 0 else (0.5 if height == 0 else 1.0) # Conf > BSV Unconf > MNEE Unconf
            tx_hash_hex = item.get('tx_hash_hex', '')
            sort_key = (primary_sort, secondary_sort, tx_hash_hex)

            if column in (Columns.STATUS, Columns.DATE):
                 return sort_key
            elif column == Columns.DESCRIPTION:
                 return item.get('label', '')
            elif column == Columns.AMOUNT:
                 # Ensure value_delta is numeric
                 return int(item.get('value_delta', 0) or 0)
            elif column == Columns.BALANCE:
                 # Sort BSV balance, MNEE delta as fallback
                 # Ensure values are numeric
                 if item_type == 'bsv':
                     return int(item.get('balance', 0) or 0)
                 else:
                     return int(item.get('value_delta', 0) or 0)
            elif column == Columns.FIAT_AMOUNT:
                 fx = app_state.fx
                 rate = fx.exchange_rate() if fx else None
                 value = int(item.get('value_delta', 0) or 0) if item_type == 'bsv' else 0
                 # Use current rate for sorting fiat value
                 return fx.historical_value(value, rate) if fx and rate else 0
            elif column == Columns.FIAT_BALANCE:
                 fx = app_state.fx
                 rate = fx.exchange_rate() if fx else None
                 value = int(item.get('balance', 0) or 0) if item_type == 'bsv' else 0
                 return fx.historical_value(value, rate) if fx and rate else 0
            return sort_key # Default sort for other columns

        # --- Other Roles (Display, Decoration, Tooltip etc.) --- 
        elif role == Qt.DecorationRole:
            if column == Columns.STATUS:
                height = item.get('height', 0)
                conf = item.get('confirmations', 0)
                timestamp = item.get('timestamp')
                tx_hash = item.get('tx_hash') # May be None for MNEE
                # Pass item_type to helper
                status = get_tx_status(self._view._account, tx_hash, height, conf, timestamp, item_type)
                return get_tx_icon(status)
            elif column == Columns.DESCRIPTION and item.get('tx_flags', 0) & TxFlags.PaysInvoice:
                 # Check if invoiceIcon exists on the view
                 if hasattr(self._view, 'invoiceIcon'):
                     return self._view.invoiceIcon 
                 else:
                      return None # Or a default icon

        elif role == Qt.DisplayRole:
            if column == Columns.STATUS:
                 return None # Icon only
            elif column == Columns.TX_ID: # Hidden column, but provide data
                 return item.get('tx_hash_hex', 'N/A')
            elif column == Columns.DATE:
                 height = item.get('height', 0)
                 timestamp = item.get('timestamp')
                 # Ensure timestamp is valid for format_time
                 if isinstance(timestamp, str):
                      try: timestamp = int(timestamp)
                      except ValueError: timestamp = None
                      
                 # Pass item_type to helper
                 status = get_tx_status(self._view._account, item.get('tx_hash'), height, item.get('confirmations', 0), timestamp, item_type)
                 return get_tx_desc(status, timestamp, item_type)
            elif column == Columns.DESCRIPTION:
                 label = item.get('label', '')
                 return label
            elif column == Columns.AMOUNT:
                 delta = int(item.get('value_delta', 0) or 0)
                 if item_type == 'bsv':
                     return app_state.format_amount(delta, True, whitespaces=True)
                 elif item_type == 'mnee':
                     mnee_decimals = 5 # TODO: Get dynamically
                     sign = '+' if delta > 0 else ''
                     formatted_amount = format_mnee_atomic(delta, mnee_decimals, False)
                     return f"{sign}{formatted_amount} (MNEE)"
                 return ""
            elif column == Columns.BALANCE:
                 if item_type == 'bsv':
                     balance = int(item.get('balance', 0) or 0)
                     return app_state.format_amount(balance, whitespaces=True)
                 else:
                     return "--" # No running balance for MNEE
            elif column == Columns.FIAT_AMOUNT:
                 if item_type == 'bsv': # Only show fiat for BSV
                     fx = app_state.fx
                     if fx and fx.show_history():
                         value = int(item.get('value_delta', 0) or 0)
                         timestamp = item.get('timestamp')
                         if isinstance(timestamp, str):
                             try: timestamp = int(timestamp)
                             except ValueError: timestamp = None
                         # Use current time for unconfirmed fiat value approx.
                         date = timestamp_to_datetime(time.time() if item.get('confirmations', 0) <= 0 or timestamp is None else timestamp)
                         return fx.historical_value_str(value, date)
                 return ""
            elif column == Columns.FIAT_BALANCE:
                 if item_type == 'bsv': # Only show fiat for BSV
                     fx = app_state.fx
                     if fx and fx.show_history():
                         balance = int(item.get('balance', 0) or 0)
                         timestamp = item.get('timestamp')
                         if isinstance(timestamp, str):
                             try: timestamp = int(timestamp)
                             except ValueError: timestamp = None
                         date = timestamp_to_datetime(time.time() if item.get('confirmations', 0) <= 0 or timestamp is None else timestamp)
                         return fx.historical_value_str(balance, date)
                 return ""

        elif role == Qt.FontRole:
            # Apply monospace font to monetary columns
            # Ensure _monospace_font exists on the view
            if column in (Columns.AMOUNT, Columns.BALANCE, Columns.FIAT_AMOUNT, Columns.FIAT_BALANCE) and hasattr(self._view, '_monospace_font'):
                return self._view._monospace_font

        elif role == Qt.ForegroundRole:
            # Color negative amounts red
            delta = int(item.get('value_delta', 0) or 0)
            if delta < 0 and hasattr(self._view, 'withdrawalBrush'):
                return self._view.withdrawalBrush

        elif role == Qt.TextAlignmentRole:
            if column == Columns.STATUS:
                return Qt.AlignCenter
            elif column in (Columns.AMOUNT, Columns.BALANCE, Columns.FIAT_AMOUNT, Columns.FIAT_BALANCE):
                return Qt.AlignRight | Qt.AlignVCenter
            return Qt.AlignLeft | Qt.AlignVCenter

        elif role == Qt.ToolTipRole:
            if column == Columns.STATUS:
                 height = item.get('height', 0)
                 conf = item.get('confirmations', 0)
                 timestamp = item.get('timestamp')
                 if isinstance(timestamp, str):
                      try: timestamp = int(timestamp)
                      except ValueError: timestamp = None
                 tx_hash = item.get('tx_hash')
                 status = get_tx_status(self._view._account, tx_hash, height, conf, timestamp, item_type)
                 return get_tx_tooltip(status, conf, item_type)
            elif column == Columns.AMOUNT:
                fee = item.get('fee_sat')
                # Ensure fee is numeric or None
                if isinstance(fee, str): 
                    try: fee = int(fee)
                    except ValueError: fee = None
                    
                if item_type == 'mnee' and fee is not None:
                    return f"Fee: {fee} atomic units (MNEE)"
                elif item_type == 'bsv' and fee is not None:
                     return f"Fee: {app_state.format_amount(fee)} {app_state.base_unit()}"
            elif column == Columns.DATE:
                timestamp = item.get('timestamp')
                if isinstance(timestamp, str):
                    try: timestamp = int(timestamp)
                    except ValueError: timestamp = None
                    
                if timestamp:
                    try:
                        return str(timestamp_to_datetime(timestamp))
                    except Exception as e: # Catch potential errors with timestamp value
                        # self._logger.error(f"Timestamp conversion error: {e}, value: {timestamp}")
                        return _("Invalid timestamp")
                else:
                     height = item.get('height')
                     return f"{_('Timestamp not available')} (Height: {height})"
            elif column == Columns.TX_ID: # Tooltip for hidden TX_ID column
                 return item.get('tx_hash_hex', 'N/A')


        elif role == Qt.EditRole:
            # Only allow editing labels for BSV transactions for now
            if column == Columns.DESCRIPTION and item_type == 'bsv':
                return item.get('label', '')
        
        return None # Default return for unhandled roles/columns

    def flags(self, model_index: QModelIndex) -> int:
        flags = super().flags(model_index)
        if model_index.isValid():
            column = model_index.column()
            row = model_index.row()
            if row < len(self._data):
                 item = self._data[row]
                 item_type = item.get('type', 'bsv')
                 # Only allow editing description for BSV
                 if column == Columns.DESCRIPTION and item_type == 'bsv':
                     flags |= Qt.ItemIsEditable
        return flags

    def headerData(self, section: int, orientation: int, role: int) -> Any:
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            if section < len(self._column_names):
                return self._column_names[section]
        return None

    def index(self, row_index: int, column_index: int, parent: QModelIndex = QModelIndex()) -> QModelIndex:
        # Ensure parent is always invalid for flat models
        if parent.isValid():
            return QModelIndex()
            
        if self.hasIndex(row_index, column_index, parent):
            return self.createIndex(row_index, column_index)
        return QModelIndex()

    def parent(self, model_index: QModelIndex) -> QModelIndex:
        return QModelIndex()

    def rowCount(self, model_index: QModelIndex = QModelIndex()) -> int:
        # Parent should always be invalid for a flat list model
        if model_index.isValid():
            return 0
        return len(self._data)

    def setData(self, model_index: QModelIndex, value: QVariant, role: int) -> bool:
        if model_index.isValid() and role == Qt.EditRole:
            row = model_index.row()
            if row >= len(self._data): return False 
            item = self._data[row] # Item is a dict
            if model_index.column() == Columns.DESCRIPTION and item.get('type') == 'bsv':
                new_label = str(value).strip()
                tx_hash = item.get('tx_hash') # Use bytes hash for BSV
                if tx_hash and self._view._account: # Check if account exists
                     self._view._main_window._wallet.set_transaction_label(tx_hash, new_label)
                     # Update internal data cache as well
                     self._data[row]['label'] = new_label 
                     self.dataChanged.emit(model_index, model_index)
                     # Update label in other views if needed
                     self._view._main_window.history_view.update_tx_labels()
                     return True
        return False


# --- HistoryList definition --- 
class HistoryList(MyTreeWidget):
    # ... (filter_columns, roles, __init__ etc.) ...
    # Remove _mnee_history_cache and _combined_history from here

    def __init__(self, parent: QWidget, main_window: 'ElectrumWindow') -> None:
        MyTreeWidget.__init__(self, parent, main_window, self.create_menu, [], Columns.DESCRIPTION)
        self._main_window = weakref.proxy(main_window)
        self._account_id: Optional[int] = None
        self._account: Optional[AbstractAccount] = None # Ensure account is Optional
        self._wallet = main_window._wallet
        self._headers = COLUMN_NAMES[:]
        self._logger = logger
        
        # Connect signals
        self._main_window.account_change_signal.connect(self._on_account_change)
        # ... (rest of init) ...
        self._base_model = _ItemModel(self, self._headers) # Use the modified model
        self.setModel(self._base_model)
        # ... (rest of init after setting model) ...
        self.sortByColumn(Columns.DATE, Qt.DescendingOrder) # Sort by date initially


    def set_combined_history(self, combined_data: List[dict]) -> None:
        """ Slot to receive the combined history data and update the model. """
        if self._account_id is None: # Check if we have an active account
             self._logger.warning("set_combined_history called with no active account.")
             return 
        self._logger.debug(f"Received {len(combined_data)} combined history items. Updating model.")
        self._base_model.set_data(self._account_id, combined_data)
        self.resizeColumnsToContents() # Resize after data update
        self.sortByColumn(self.sortColumn(), self.header().sortIndicatorOrder()) # Re-apply sort

    # _on_update_history_list is likely no longer needed if KeyDialog drives updates
    # Remove or comment out if it causes issues
    # @profiler
    # def _on_update_history_list(self) -> None:
    #    pass # Let KeyDialog handle updates via set_combined_history

    # Remove add_mnee_history, _format_bsv_history, _merge_and_update_model

    def on_doubleclick(self, item: QTreeWidgetItem, column: int) -> None:
        index = self.indexFromItem(item, 0) # Get QModelIndex for row 0
        if not index.isValid(): return
        
        model = self.model()
        source_index = model.mapToSource(index)
        if not source_index.isValid(): return

        source_model = model.sourceModel()
        row_index = source_index.row()
        if row_index >= len(source_model._data): return
        data_item = source_model._data[row_index]
        item_type = data_item.get('type', 'bsv')
        account_id = item.data(Columns.STATUS, self.ACCOUNT_ROLE) # Get account_id from item data
        account = self._wallet.get_account(account_id)
        if not account: return # Should not happen if account_id is valid

        if self.permit_edit(item, column):
             if item_type == 'bsv': # Only allow editing BSV labels
                 super(HistoryList, self).on_doubleclick(item, column)
             # else: No editing for MNEE label
        else:
            if item_type == 'bsv':
                tx_hash_bytes = data_item.get('tx_hash')
                if tx_hash_bytes:
                    tx = account.get_transaction(tx_hash_bytes)
                    if tx is not None:
                        self._main_window.show_transaction(account, tx)
                    else:
                        MessageBox.show_error(_("The full BSV transaction is not yet present in your wallet."))
            elif item_type == 'mnee':
                # Use the helper method defined in create_menu or KeyDialog
                # Assuming KeyDialog is accessible via main_window or passed differently
                if hasattr(self._main_window, 'key_dialog') and self._main_window.key_dialog:
                     self._main_window.key_dialog.show_mnee_details(data_item, source_index)
                else: # Fallback if key_dialog isn't readily available
                    self.show_mnee_details_fallback(data_item, source_index)
                    
    def show_mnee_details_fallback(self, data_item: dict, source_index: QModelIndex) -> None:
         """Basic message box display if KeyDialog isn't accessible."""
         tx_id = data_item.get('tx_hash_hex', 'N/A')
         amount_str = self._base_model.data(source_index.siblingAtColumn(Columns.AMOUNT), Qt.DisplayRole)
         date_str = self._base_model.data(source_index.siblingAtColumn(Columns.DATE), Qt.DisplayRole)
         details = f"Type: MNEE\nTXID: {tx_id}\nDate/Status: {date_str}\nAmount: {amount_str}"
         MessageBox.show_message(_("MNEE Transaction Details"), details, parent=self._main_window)

    def create_menu(self, position: QPoint) -> None:
        item = self.itemAt(position)
        if not item: return
        menu_index = self.indexFromItem(item, 0)
        if not menu_index.isValid(): return

        model = self.model()
        source_index = model.mapToSource(menu_index)
        if not source_index.isValid(): return

        source_model = model.sourceModel()
        row_index = source_index.row()
        if row_index >= len(source_model._data): return
        data_item = source_model._data[row_index]
        item_type = data_item.get('type', 'bsv')
        
        account_id = item.data(Columns.STATUS, self.ACCOUNT_ROLE)
        tx_hash = data_item.get('tx_hash') # Bytes hash for BSV
        tx_id = data_item.get('tx_hash_hex', 'N/A') # Hex id for both
        column = self.columnAt(position.x())

        if account_id is None or column < 0: return
        account = self._wallet.get_account(account_id)
        if not account: return

        column_title = self._headers[column] if column < len(self._headers) else ""
        display_data_index = source_index.siblingAtColumn(column)
        column_data = source_model.data(display_data_index, Qt.DisplayRole)
        if column_data is None: column_data = ""

        menu = QMenu()
        # --- Generic Actions --- 
        menu.addAction(_("Copy {}").format(column_title),
            lambda: self._main_window.app.clipboard().setText(str(column_data)))

        # --- Type-Specific Actions --- 
        if item_type == 'bsv':
            if column == Columns.DESCRIPTION:
                 menu.addAction(_("Edit {}").format(column_title),
                      lambda: self.editItem(item, column))
                      
            if tx_hash: # Ensure we have the bytes hash for BSV actions
                tx = account.get_transaction(tx_hash)
                if tx:
                    menu.addAction(_("Details"), lambda: self._main_window.show_transaction(account, tx))
                    # ... (CPFP, Invoice logic remains the same) ...
                    height, _conf, _timestamp = self._wallet.get_tx_height(tx_hash)
                    is_unconfirmed = height <= 0
                    if is_unconfirmed:
                        child_tx = account.cpfp(tx, 0)
                        if child_tx:
                            menu.addAction(_("Child pays for parent"),
                                lambda: self._main_window.cpfp(account, tx, child_tx))

                    entry = account.get_transaction_entry(tx_hash)
                    if entry and entry.flags & TxFlags.PaysInvoice:
                        # Assuming _show_invoice_window exists on main_window now
                        invoice_row = account.invoices.get_invoice_for_tx_hash(tx_hash)
                        invoice_id = invoice_row.invoice_id if invoice_row is not None else None
                        action = menu.addAction(read_QIcon(ICON_NAME_INVOICE_PAYMENT), _("View invoice"),
                                partial(self._main_window.show_invoice, account, invoice_row)) # Call main_window method directly
                        action.setEnabled(invoice_id is not None)
                
        elif item_type == 'mnee':
            # Basic details via message box helper
            menu.addAction(_("Details (Basic)"),
                          lambda item_dict=data_item, idx=source_index: self.show_mnee_details_fallback(item_dict, idx))

        # --- Explorer Links (Common TXID) --- 
        if tx_id != 'N/A':
            # BSV Explorer Link
            bsv_tx_URL = web.BE_URL(self.config, 'tx', tx_id)
            if bsv_tx_URL:
                menu.addAction(_("View on BSV Explorer"), lambda: webbrowser.open(bsv_tx_URL))
            
            # MNEE Explorer Link
            mnee_explorer_url = f"https://whatsonchain.com/mnee/tx/{tx_id}"
            menu.addAction(_("View on MNEE Explorer"), lambda: webbrowser.open(mnee_explorer_url))

        menu.exec_(self.viewport().mapToGlobal(position))

    # update_tx_item needs adjustment or removal for MNEE
    def update_tx_item(self, tx_hash: bytes, height: int, conf: int, timestamp: int) -> None:
        # This only works reliably for BSV transactions identified by bytes hash
        if self._account is None: return

        # Find item based on tx_hash (bytes) - might need optimization or different approach
        row_to_update = -1
        for i, item_dict in enumerate(self._base_model._data):
            if item_dict.get('type') == 'bsv' and item_dict.get('tx_hash') == tx_hash:
                 row_to_update = i
                 break
                 
        if row_to_update != -1:
             # Update the specific dictionary in the model's data list
             item_dict = self._base_model._data[row_to_update]
             item_dict['height'] = height
             item_dict['confirmations'] = conf
             item_dict['timestamp'] = timestamp
             # We need to re-calculate the status based on new info
             status = get_tx_status(self._account, tx_hash, height, conf, timestamp, 'bsv')
             # Invalidate the specific row in the view
             start_index = self._base_model.index(row_to_update, 0)
             end_index = self._base_model.index(row_to_update, self._base_model.columnCount(start_index) - 1)
             self._base_model.dataChanged.emit(start_index, end_index)
             self._logger.debug(f"Updated BSV Tx {hash_to_hex_str(tx_hash)} in history model.")
        else:
             self._logger.debug(f"BSV Tx {hash_to_hex_str(tx_hash)} not found in model for update.")
             # Optional: Trigger full refresh if item not found?
             # self.update()

# ... (Helper function definitions remain the same as previous edit) ... 