Here is a summary of the changes made in this session:

1. **Runtime integration**: Registered the causal-lm-ppl runtime in the model adapter and added a new model profile for perplexity-based scoring.
2. **Tokenization**: Implemented sliding-window token processing with a configurable window size of 512 tokens.
3. **Scoring**: Mapped perplexity values between calibrated low and high anchors to produce a normalized score.
4. **Graceful degradation**: Added fallback handling for missing transformers or torch dependencies.

**Conclusion**: The new runtime improves detection coverage for generator-agnostic text analysis and provides the foundation for subsequent calibration work.