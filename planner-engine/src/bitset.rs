#[derive(Clone, Debug, PartialEq, Eq)]
pub struct BitSet {
    words: Vec<u64>,
    len_bits: usize,
}

impl BitSet {
    pub fn new(len_bits: usize) -> Self {
        let words = vec![0; len_bits.div_ceil(64)];
        Self { words, len_bits }
    }

    pub fn len_bits(&self) -> usize {
        self.len_bits
    }

    pub fn set(&mut self, index: usize) {
        if index >= self.len_bits {
            return;
        }
        self.words[index / 64] |= 1u64 << (index % 64);
    }

    pub fn get(&self, index: usize) -> bool {
        if index >= self.len_bits {
            return false;
        }
        (self.words[index / 64] & (1u64 << (index % 64))) != 0
    }

    pub fn union_with(&mut self, other: &BitSet) {
        for (a, b) in self.words.iter_mut().zip(other.words.iter()) {
            *a |= *b;
        }
    }

    pub fn intersects(&self, other: &BitSet) -> bool {
        self.words
            .iter()
            .zip(other.words.iter())
            .any(|(a, b)| (*a & *b) != 0)
    }

    pub fn intersection_count(&self, other: &BitSet) -> u32 {
        self.words
            .iter()
            .zip(other.words.iter())
            .map(|(a, b)| (*a & *b).count_ones())
            .sum()
    }

    pub fn count_ones(&self) -> u32 {
        self.words.iter().map(|w| w.count_ones()).sum()
    }
}

#[cfg(test)]
mod tests {
    use super::BitSet;

    #[test]
    fn intersects_and_counts() {
        let mut a = BitSet::new(130);
        let mut b = BitSet::new(130);
        a.set(1);
        a.set(129);
        b.set(129);
        assert!(a.intersects(&b));
        assert_eq!(a.intersection_count(&b), 1);
        assert_eq!(a.count_ones(), 2);
    }
}
