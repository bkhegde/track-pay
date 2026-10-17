Application Name: Track-Pay 

Features:

1. Maintain customer list including Name, Phone num, monthly fee, status, one or more Indian upi-ids (Complete CRUD feature). Use check box method to activate or de-activate customers individually or in bulk.
2. Option to generate invoice entry for a given month for each active customers. Idempotency to be maintained. Provide option to update the invoice amount individually or in bulk for a given month.

3\. Option to mark invoices as paid by manually entering the paid amount and date (optional) against the invoice

4\. Option to upload bank statement. Upon upload, store the bank statement entries in a database table maintaining the idempotency. 

5\. Auto reconcile option to programmatically match the bank statement entries with the invoices. Use upi-id for matching. On successful match, update the invoice as paid by entering the amount, date of payment and transaction id from bank statement

6\. Provide option to manually view the invoice and the bank statement side by side to manually match the payment records.

7\. Provide option to filter to view and edit only the unpaid invoices





Use a good quality html grid for viewing and editing the data.



