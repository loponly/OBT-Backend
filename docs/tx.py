def create_pseudo_tx(bot, type, amount, fee=0.001):
        template = bot['state'].trade_log[-1].copy()
        print('Before', bot['state'].curBalance, bot['state'].tokBalance)
        invert_v = (-1 if type == 'SELL' else 1)
        fee_t, fee_c = fee, 0
        if type == 'SELL':
            fee_t, fee_c = fee_c, fee_t
        fee_t, fee_c = fee_t * amount, fee_c * amount * template['price']
        bot['state'].curBalance -= amount * template['price'] * invert_v
        bot['state'].tokBalance += amount * invert_v 
        bot['state'].curBalance -= fee_c
        bot['state'].tokBalance -= fee_t
        template['type'] = type
        template['amount'] = amount
        template['date'] += 4 * 60 * 60
        template['fee'] = fee_t or fee_c
        template['fee_asset'] = bot['market'].split(':')[0 if type == 'BUY' else 1]
        template['change'] = 0
        print('After', bot['state'].curBalance, bot['state'].tokBalance, template)
        bot['state'].trade_log.append(template)
