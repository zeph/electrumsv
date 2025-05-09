from typing import Sequence, List, Optional, TYPE_CHECKING
import weakref

from electrumsv.wallet_database.tables import TransactionDeltaKeySummaryRow
from electrumsv.constants import DerivationType, ScriptType, KeyInstanceFlag

if TYPE_CHECKING:
    from electrumsv.wallet import AbstractAccount

class KeyService:
    def __init__(self, account: "AbstractAccount") -> None:
        self._account = weakref.proxy(account)

    def get_key_summaries(self, keyinstance_ids: Optional[Sequence[int]]=None) \
            -> List[TransactionDeltaKeySummaryRow]:
        wallet = self._account.get_wallet()
        account = self._account
        mnee_balances = getattr(account, '_mnee_balance_per_key', {})

        with wallet.get_transaction_delta_table() as table:
            base_summaries: List[tuple] = table.read_key_summary(account.get_id(), keyinstance_ids)
        
        augmented_summaries = []
        # Indexes based on READ_KEY_SUMMARY_SQL select order
        # KI.keyinstance_id, KI.masterkey_id, KI.derivation_type, KI.derivation_data, 
        # KI.script_type, KI.flags, KI.date_updated, TOTAL(TD.value_delta), COUNT(TD.value_delta)
        #   0                 1                2                   3
        #   4                5           6                   7                       8
        for row_tuple in base_summaries:
            key_id = row_tuple[0]
            mnee_balance = mnee_balances.get(key_id, 0)
            
            # Adjust match_count based on MNEE balance
            original_match_count = row_tuple[8]
            final_match_count = original_match_count
            if mnee_balance > 0 and original_match_count == 0:
                final_match_count = 1 # Indicate usage if MNEE balance > 0 and BSV usage is 0
                
            augmented_summaries.append(TransactionDeltaKeySummaryRow(
                keyinstance_id=key_id,
                masterkey_id=row_tuple[1],
                derivation_type=DerivationType(row_tuple[2]), # Convert int to Enum
                derivation_data=row_tuple[3],
                script_type=ScriptType(row_tuple[4]), # Convert int to Enum
                flags=KeyInstanceFlag(row_tuple[5]), # Convert int to Enum
                date_updated=row_tuple[6],
                total_value=row_tuple[7] if row_tuple[7] is not None else 0, # TOTAL returns None if no rows
                match_count=final_match_count, # Use the adjusted count
                mnee_balance=mnee_balance
            ))
        return augmented_summaries

