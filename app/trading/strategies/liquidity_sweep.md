# Liquidity Sweep Strategy

Liquidity sweep — снятие ликвидности за локальным high/low с быстрым возвратом в диапазон.

Long setup:
1. Цена прокалывает локальный low.
2. Свеча возвращается выше уровня.
3. Желателен volume spike на выносе.
4. Entry после подтверждения удержания уровня.
5. Stop ниже sweep-low.
6. Targets: mid-range, ближайший high, зона ликвидности.

Short setup:
1. Цена прокалывает локальный high.
2. Свеча возвращается ниже уровня.
3. Желателен volume spike.
4. Stop выше sweep-high.

No trade:
- нет возврата в диапазон;
- sweep против сильного тренда без подтверждения;
- R/R < 1:2.
